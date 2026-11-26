"""Agent Builder servis katmanı: CRUD, taslak, sürümler, varsayılan agent, schedule bağımlılıkları, run çözümü.

Gömülü SRE Agent `system` kaydıdır (SYSTEM_AGENT_ID): listelenir ve seçilebilir; workflow'u kodda olduğu için
düzenlenemez, kopyalanamaz ve dışa aktarılamaz.
"""
from __future__ import annotations

import hashlib
import json
import re
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from airlab_agent.errors import UserError

from ..db.models import (Agent, AgentRun, AgentVersion, KeyValue, LlmProfile, ScheduledJob, SYSTEM_AGENT_ID, User)
from ..repositories.base import Conflict, NotFound
from ..security import audit
from . import definition as D
from . import validate as V

DEFAULT_KEY = "agent_builder.default_chat_agent"
HC_AGENT_KEY = "agent_builder.schedule_agent.health-check"
AGENT_JOB_TYPE = "agent_run"
AGENT_JOB_PREFIX = "agent:"


class NotRunnable(Exception):
    """Agent seçilen modda çalıştırılamaz (yayımlanmamış / arşivlenmiş / mod desteklenmiyor / erişim yok)."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code, self.message = code, message


class Invalid(Exception):
    """Doğrulama hataları (node / alan bazında)."""

    def __init__(self, issues: list[dict], message: str = "agent tanımı geçersiz"):
        super().__init__(message)
        self.issues, self.message = issues, message


def checksum(defn: dict) -> str:
    return hashlib.sha256(json.dumps(defn, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def slug(name: str) -> str:
    tr = str.maketrans("çğıöşüÇĞİÖŞÜ", "cgiosuCGIOSU")
    s = re.sub(r"[^a-z0-9]+", "-", name.translate(tr).lower()).strip("-")[:40] or "agent"
    return f"{s}-{uuid.uuid4().hex[:6]}"


async def get(s: AsyncSession, agent_id: uuid.UUID | str) -> Agent:
    try:
        aid = uuid.UUID(str(agent_id))
    except ValueError:
        raise NotFound("agent bulunamadı") from None
    a = await s.get(Agent, aid)
    if a is None:
        raise NotFound("agent bulunamadı")
    return a


def _custom(a: Agent) -> None:
    if a.kind != "custom":
        raise UserError("gömülü SRE Agent'ın davranışı platform kodunda tanımlıdır; düzenlenemez")


async def _name_free(s: AsyncSession, name: str, exclude: uuid.UUID | None = None) -> None:
    q = select(Agent.id).where(func.lower(Agent.name) == name.strip().lower(), Agent.status != "archived")
    if exclude:
        q = q.where(Agent.id != exclude)
    if (await s.execute(q)).first():
        raise Conflict(f"'{name.strip()}' adında bir agent zaten var; farklı bir ad seçin")


def _model_view(a: Agent, defn: dict | None, profiles: dict[str, dict]) -> dict | None:
    if a.kind == "system":
        return {"model": None, "profile": "Settings › LLM (platform)", "provider": "anthropic"}
    m = ((defn or {}).get("settings") or {}).get("model") or {}
    p = profiles.get(str(m.get("llm_profile_id"))) if m.get("llm_profile_id") else None
    return {"model": m.get("model") or (p or {}).get("model"), "profile": (p or {}).get("name"),
            "provider": (p or {}).get("provider")}


async def _profiles(s: AsyncSession) -> dict[str, dict]:
    return {str(p.id): {"name": p.name, "model": p.model, "provider": p.provider}
            for p in (await s.execute(select(LlmProfile))).scalars()}


async def last_runs(s: AsyncSession, ids: list[uuid.UUID]) -> dict[uuid.UUID, dict]:
    if not ids:
        return {}
    sub = (select(AgentRun.agent_id, func.max(AgentRun.started_at).label("mx"))
           .where(AgentRun.agent_id.in_(ids)).group_by(AgentRun.agent_id).subquery())
    rows = (await s.execute(select(AgentRun).join(sub, (AgentRun.agent_id == sub.c.agent_id)
                                                  & (AgentRun.started_at == sub.c.mx)))).scalars()
    return {r.agent_id: {"run_id": r.run_id, "status": r.status, "mode": r.mode, "started_at": r.started_at.isoformat(),
                         "version_label": r.version_label, "error": r.error} for r in rows}


async def ensure_system_agent(s: AsyncSession) -> None:
    """Gömülü SRE Agent satırı her zaman vardır (idempotent; mevcut satıra dokunmaz)."""
    from sqlalchemy.dialects.postgresql import insert
    await s.execute(insert(Agent).values(id=SYSTEM_AGENT_ID, key="sre-agent", name="SRE Agent", kind="system",
                                         status="published", modes=["chat", "scheduled"], draft={},
                                         description="Gömülü SRE Agent: Dynatrace MCP ile telemetri analizi, "
                                                     "deterministik risk score, RCA ve geçmiş kayıtlarla "
                                                     "karşılaştırma. Davranışı platform kodunda tanımlıdır.",
                                         created_by="system", updated_by="system")
                    .on_conflict_do_nothing(index_elements=["id"]))


async def default_chat(s: AsyncSession) -> uuid.UUID:
    kv = await s.get(KeyValue, DEFAULT_KEY)
    try:
        return uuid.UUID((kv.value or {}).get("agent_id")) if kv else SYSTEM_AGENT_ID
    except (TypeError, ValueError):
        return SYSTEM_AGENT_ID


async def summary(s: AsyncSession, a: Agent, *, profiles: dict | None = None, last: dict | None = None,
                  default_id: uuid.UUID | None = None) -> dict:
    profiles = profiles if profiles is not None else await _profiles(s)
    ver = None
    if a.active_version and a.kind == "custom":
        ver = (await s.execute(select(AgentVersion).where(AgentVersion.agent_id == a.id,
                                                          AgentVersion.version == a.active_version))).scalar_one_or_none()
    defn = ver.definition if ver is not None else a.draft
    return {"id": str(a.id), "key": a.key, "name": a.name, "description": a.description, "kind": a.kind,
            "status": a.status, "modes": list(a.modes or []), "active_version": a.active_version,
            "published_version_label": ("builtin" if a.kind == "system" else f"v{a.active_version}"
                                        if a.active_version else None),
            "model": _model_view(a, defn, profiles), "draft_revision": a.draft_revision,
            "has_unpublished_changes": a.kind == "custom" and (ver is None or ver.draft_revision != a.draft_revision),
            "last_run": (last or {}).get(a.id), "is_default_chat": default_id == a.id,
            "can_copy": a.kind == "custom", "can_export": a.kind == "custom",
            "copy_unavailable_reason": None if a.kind == "custom" else
            "Gömülü agent'ın workflow'u platform kodundadır; canvas tanımına dönüştürülemez",
            "created_by": a.created_by, "updated_by": a.updated_by,
            "created_at": a.created_at.isoformat() if a.created_at else None,
            "updated_at": a.updated_at.isoformat() if a.updated_at else None}


async def list_(s: AsyncSession, *, include_archived: bool = False, mode: str | None = None,
                runnable: bool = False) -> list[dict]:
    q = select(Agent).order_by(Agent.kind.desc(), Agent.name)
    if not include_archived:
        q = q.where(Agent.status != "archived")
    agents = list((await s.execute(q)).scalars())
    if mode:
        agents = [a for a in agents if mode in (a.modes or [])]
    if runnable:
        agents = [a for a in agents if a.kind == "system" or (a.status == "published" and a.active_version)]
    profiles, last = await _profiles(s), await last_runs(s, [a.id for a in agents])
    dflt = await default_chat(s)
    return [await summary(s, a, profiles=profiles, last=last, default_id=dflt) for a in agents]


async def detail(rt, s: AsyncSession, a: Agent) -> dict:
    out = await summary(s, a, last=await last_runs(s, [a.id]), default_id=await default_chat(s))
    vers = (await s.execute(select(AgentVersion).where(AgentVersion.agent_id == a.id)
                            .order_by(AgentVersion.version.desc()))).scalars()
    out["versions"] = [{"version": v.version, "checksum": v.checksum[:12], "note": v.note,
                        "published_by": v.published_by, "published_at": v.published_at.isoformat(),
                        "draft_revision": v.draft_revision, "active": v.version == a.active_version} for v in vers]
    out["draft"] = a.draft if a.kind == "custom" else None
    out["source"] = a.source or {}
    if a.kind == "custom":
        env = await V.load_env(rt, s)
        _, issues = V.run_all(a.draft, env, name=a.name, modes=list(a.modes or []), stage="publish")
        out["issues"] = issues
        out["resolved_models"] = _resolved(a.draft, env)
    else:
        out["issues"] = []
    out["dependencies"] = await dependencies(s, a.id)
    return out


def _resolved(raw: dict, env: V.Env) -> dict[str, dict]:
    try:
        d = D.parse(raw)
    except Exception:  # noqa: BLE001
        return {}
    out = {}
    for n in d.workflow.nodes:
        if n.type in ("llm", "structured_output"):
            r = V.resolve_model(d, n, env)
            out[n.id] = {k: r[k] for k in ("profile_id", "profile", "provider", "model", "source")}
    return out


async def create(s: AsyncSession, *, name: str, description: str | None, modes: list[str], actor: str,
                 definition: dict | None = None, source: dict | None = None) -> Agent:
    name = (name or "").strip()
    if not name:
        raise UserError("agent adı zorunlu")
    await _name_free(s, name)
    defn = definition if definition is not None else D.default_definition(name)
    d, issues = V.structural(defn, name=name, modes=modes)
    if V.blocking(issues):
        raise Invalid(V.blocking(issues))
    a = Agent(key=slug(name), name=name[:100], description=(description or "")[:2000] or None, kind="custom",
              status="draft", modes=list(dict.fromkeys(modes)), draft=d.model_dump(mode="json"), created_by=actor,
              updated_by=actor, source=source or {})
    s.add(a)
    await s.flush()
    await audit.record(s, actor=actor, action="agent.create", section="agent_builder", target=str(a.id),
                       after={"name": a.name, "modes": a.modes, **({"source": source} if source else {})})
    return a


async def save_draft(rt, s: AsyncSession, a: Agent, *, revision: int, name: str, description: str | None,
                     modes: list[str], definition: dict, actor: str) -> tuple[Agent, list[dict]]:
    _custom(a)
    if a.status == "archived":
        raise UserError("arşivlenmiş agent düzenlenemez")
    if revision != a.draft_revision:
        raise Conflict(f"taslak başka bir oturumda değişti (revizyon {a.draft_revision}); sayfayı yenileyin")
    if name.strip().lower() != a.name.lower():
        await _name_free(s, name, exclude=a.id)
    env = await V.load_env(rt, s)
    d, issues = V.run_all(definition, env, name=name, modes=modes, stage="save")
    if V.blocking(issues):
        raise Invalid(V.blocking(issues), "taslak kaydedilemedi: yapısal hatalar var")
    a.name, a.description, a.modes = name.strip()[:100], (description or "")[:2000] or None, list(dict.fromkeys(modes))
    a.draft = d.model_dump(mode="json")
    a.draft_revision += 1
    a.updated_by = actor
    a.updated_at = datetime.now(timezone.utc)
    await s.flush()
    return a, issues


async def copy(s: AsyncSession, a: Agent, *, name: str | None, actor: str) -> Agent:
    _custom(a)
    nm = (name or f"{a.name} (kopya)").strip()
    return await create(s, name=nm, description=a.description, modes=list(a.modes or []), actor=actor,
                        definition=a.draft, source={"copied_from": str(a.id), "copied_from_name": a.name,
                                                    "draft_revision": a.draft_revision})


async def publish(rt, s: AsyncSession, a: Agent, *, revision: int, note: str | None, actor: str) -> AgentVersion:
    _custom(a)
    if a.status == "archived":
        raise UserError("arşivlenmiş agent yayımlanamaz")
    if revision != a.draft_revision:
        raise Conflict("yayımlanacak taslak değişti; sayfayı yenileyip tekrar deneyin")
    env = await V.load_env(rt, s)
    d, issues = V.run_all(a.draft, env, name=a.name, modes=list(a.modes or []), stage="publish")
    if V.blocking(issues):
        raise Invalid(V.blocking(issues), "yayımlanamadı: doğrulama hataları var")
    defn = {**d.model_dump(mode="json"), "name": a.name, "description": a.description, "modes": list(a.modes or [])}
    nxt = ((await s.execute(select(func.max(AgentVersion.version)).where(AgentVersion.agent_id == a.id))).scalar() or 0) + 1
    v = AgentVersion(agent_id=a.id, version=nxt, definition=defn, checksum=checksum(defn),
                     draft_revision=a.draft_revision, note=(note or "")[:500] or None, published_by=actor)
    s.add(v)
    prev = a.active_version
    a.active_version, a.status, a.updated_by = nxt, "published", actor
    await s.flush()
    await audit.record(s, actor=actor, action="agent.publish", section="agent_builder", target=str(a.id),
                       revision=nxt, before={"active_version": prev},
                       after={"active_version": nxt, "checksum": v.checksum[:16], "modes": a.modes,
                              "models": sorted({m.get("model") or "" for m in _resolved(a.draft, env).values()})})
    return v


async def version(s: AsyncSession, a: Agent, n: int) -> AgentVersion:
    v = (await s.execute(select(AgentVersion).where(AgentVersion.agent_id == a.id, AgentVersion.version == n))
         ).scalar_one_or_none()
    if v is None:
        raise NotFound(f"sürüm bulunamadı: v{n}")
    return v


async def activate(s: AsyncSession, a: Agent, n: int, *, actor: str) -> Agent:
    _custom(a)
    if a.status == "archived":
        raise UserError("arşivlenmiş agent'ta sürüm aktif edilemez")
    await version(s, a, n)
    prev = a.active_version
    a.active_version, a.status, a.updated_by = n, "published", actor
    await s.flush()
    await audit.record(s, actor=actor, action="agent.version.activate", section="agent_builder", target=str(a.id),
                       revision=n, before={"active_version": prev}, after={"active_version": n})
    return a


async def dependencies(s: AsyncSession, agent_id: uuid.UUID) -> list[dict]:
    """Agent'ı kullanan aktif seçimler: varsayılan soru-cevap agent'ı ve schedule'lar."""
    out = []
    if await default_chat(s) == agent_id and agent_id != SYSTEM_AGENT_ID:
        out.append({"kind": "default_chat", "name": "Soru-Cevap varsayılan agent'ı",
                    "fix": "Agent Builder › Soru-Cevap varsayılanı"})
    for j in (await s.execute(select(ScheduledJob).where(ScheduledJob.job_type == AGENT_JOB_TYPE))).scalars():
        if str((j.params or {}).get("agent_id")) == str(agent_id):
            out.append({"kind": "schedule", "name": j.name, "enabled": j.enabled,
                        "fix": "Settings › Scheduling › Agent schedule'ları"})
    kv = await s.get(KeyValue, HC_AGENT_KEY)
    if kv and str((kv.value or {}).get("agent_id")) == str(agent_id):
        out.append({"kind": "schedule", "name": "health-check", "enabled": True,
                    "fix": "Settings › Scheduling › Agent schedule'ları"})
    return out


async def archive(s: AsyncSession, a: Agent, *, actor: str) -> Agent:
    _custom(a)
    deps = await dependencies(s, a.id)
    if deps:
        raise Conflict("agent kullanımda; önce şu seçimleri değiştirin: " +
                       ", ".join(f"{d['name']}" for d in deps))
    prev = a.status
    a.status, a.archived_at, a.updated_by = "archived", datetime.now(timezone.utc), actor
    await s.flush()
    await audit.record(s, actor=actor, action="agent.archive", section="agent_builder", target=str(a.id),
                       before={"status": prev}, after={"status": "archived"})
    return a


async def set_default(s: AsyncSession, agent_id: uuid.UUID, *, actor: str) -> dict:
    a = await get(s, agent_id)
    if a.kind == "custom":
        _runnable(a, "chat")
    prev = await default_chat(s)
    kv = await s.get(KeyValue, DEFAULT_KEY)
    if kv is None:
        s.add(KeyValue(key=DEFAULT_KEY, value={"agent_id": str(a.id)}))
    else:
        kv.value = {"agent_id": str(a.id)}
    await s.flush()
    await audit.record(s, actor=actor, action="agent.default.change", section="agent_builder", target="chat",
                       before={"agent_id": str(prev)}, after={"agent_id": str(a.id), "name": a.name})
    return {"agent_id": str(a.id), "name": a.name}


# ------------------------------------------------------------------------------------------------ çalıştırma çözümü
def _runnable(a: Agent, mode: str) -> None:
    if a.status == "archived":
        raise NotRunnable("agent_archived", f"'{a.name}' arşivlenmiş; çalıştırılamaz")
    if mode not in (a.modes or []):
        tr = {"chat": "soru-cevap", "scheduled": "zamanlanmış çalışma"}.get(mode, mode)
        raise NotRunnable("mode_unsupported", f"'{a.name}' {tr} modunu desteklemiyor")
    if a.kind == "custom" and (a.status != "published" or not a.active_version):
        raise NotRunnable("agent_unpublished", f"'{a.name}' yayımlanmamış; önce Agent Builder'da yayımlayın")


async def resolve(s: AsyncSession, agent_id: uuid.UUID | str | None, mode: str, *,
                  pinned_version: int | None = None) -> tuple[Agent, AgentVersion | None]:
    """Koşu başında agent + o an aktif yayımlanmış sürüm (retry'da `pinned_version` korunur). Sistem → (agent, None)."""
    try:
        a = await get(s, agent_id or SYSTEM_AGENT_ID)
    except NotFound:
        raise NotRunnable("agent_not_found", "seçilen agent bulunamadı (silinmiş olabilir)") from None
    _runnable(a, mode)
    if a.kind == "system":
        return a, None
    v = await version(s, a, pinned_version or a.active_version)
    return a, v


async def check_actor(s: AsyncSession, username: str | None) -> None:
    """Schedule'ı oluşturan kullanıcının erişimi sürüyor mu (aktif + agent:run yetkisi)."""
    from ..security.permissions import P, has
    if not username or username.startswith("system"):
        return
    u = (await s.execute(select(User).where(User.username == username))).scalar_one_or_none()
    if u is None or not u.is_active or not has(u.role, P.AGENT_RUN):
        raise NotRunnable("access_revoked", f"schedule sahibi '{username}' için agent çalıştırma erişimi kaldırılmış")


# ------------------------------------------------------------------------------------------------ schedule'lar
def _job_out(j: ScheduledJob, names: dict[str, str]) -> dict:
    p = j.params or {}
    return {"name": j.name, "kind": "agent", "agent_id": p.get("agent_id"), "agent_name": names.get(str(p.get("agent_id"))),
            "interval_minutes": j.interval_seconds // 60, "enabled": j.enabled, "input": p.get("input") or "",
            "variables": p.get("variables") or {}, "created_by": p.get("created_by"),
            "next_run_at": j.next_run_at.isoformat() if j.next_run_at else None,
            "last_run_at": j.last_run_at.isoformat() if j.last_run_at else None}


async def schedules(s: AsyncSession) -> list[dict]:
    names = {str(a.id): a.name for a in (await s.execute(select(Agent))).scalars()}
    out = []
    hc = (await s.execute(select(ScheduledJob).where(ScheduledJob.name == "health-check"))).scalar_one_or_none()
    if hc is not None:
        kv = await s.get(KeyValue, HC_AGENT_KEY)
        aid = (kv.value or {}).get("agent_id") if kv else str(SYSTEM_AGENT_ID)
        out.append({"name": "health-check", "kind": "system", "agent_id": aid, "agent_name": names.get(str(aid)),
                    "interval_minutes": hc.interval_seconds // 60, "enabled": hc.enabled,
                    "input": "Zamanlanmış sağlık kontrolü (Settings › Scheduling aralığı / penceresi)",
                    "variables": {}, "created_by": "system",
                    "next_run_at": hc.next_run_at.isoformat() if hc.next_run_at else None,
                    "last_run_at": hc.last_run_at.isoformat() if hc.last_run_at else None})
    jobs = (await s.execute(select(ScheduledJob).where(ScheduledJob.job_type == AGENT_JOB_TYPE)
                            .order_by(ScheduledJob.name))).scalars()
    return out + [_job_out(j, names) for j in jobs]


async def _schedulable(s: AsyncSession, agent_id: str) -> Agent:
    a = await get(s, agent_id)
    if a.status == "archived":
        raise UserError("arşivlenmiş agent seçilemez")
    if "scheduled" not in (a.modes or []):
        raise UserError(f"'{a.name}' zamanlanmış çalışma modunu desteklemiyor")
    return a


async def upsert_schedule(s: AsyncSession, *, name: str | None, agent_id: str, interval_minutes: int,
                          enabled: bool, input_: str, variables: dict, actor: str, create: bool) -> dict:
    a = await _schedulable(s, agent_id)
    if not 1 <= interval_minutes <= 10080:
        raise UserError("aralık 1 dk – 7 gün olmalı")
    if name != "health-check" and a.kind == "system":
        raise UserError("gömülü SRE Agent'ın zamanlanmış çalışması health-check schedule'ıdır; yeni schedule için "
                        "özel agent seçin")
    if name == "health-check":
        kv = await s.get(KeyValue, HC_AGENT_KEY)
        before = (kv.value if kv else {}).get("agent_id") if kv else str(SYSTEM_AGENT_ID)
        if kv is None:
            s.add(KeyValue(key=HC_AGENT_KEY, value={"agent_id": str(a.id), "set_by": actor}))
        else:
            kv.value = {"agent_id": str(a.id), "set_by": actor}
        await s.flush()
        await audit.record(s, actor=actor, action="schedule.agent.change", section="scheduling", target="health-check",
                           before={"agent_id": before}, after={"agent_id": str(a.id), "name": a.name})
        return {"name": "health-check", "agent_id": str(a.id)}
    params = {"agent_id": str(a.id), "input": (input_ or "")[:4000],
              "variables": {str(k)[:40]: str(v)[:2000] for k, v in (variables or {}).items()}, "created_by": actor}
    if create:
        label = re.sub(r"[^a-z0-9-]+", "-", (name or a.key).lower()).strip("-")[:60] or a.key
        jname = f"{AGENT_JOB_PREFIX}{label}"
        if (await s.execute(select(ScheduledJob).where(ScheduledJob.name == jname))).first():
            raise Conflict(f"'{jname}' adında bir schedule var")
        j = ScheduledJob(name=jname, job_type=AGENT_JOB_TYPE, interval_seconds=interval_minutes * 60,
                         enabled=enabled, params=params)
        s.add(j)
        action = "schedule.create"
    else:
        j = (await s.execute(select(ScheduledJob).where(ScheduledJob.name == name,
                                                        ScheduledJob.job_type == AGENT_JOB_TYPE))).scalar_one_or_none()
        if j is None:
            raise NotFound("schedule bulunamadı")
        params["created_by"] = (j.params or {}).get("created_by") or actor
        j.params, j.interval_seconds, j.enabled = params, interval_minutes * 60, enabled
        action = "schedule.update"
    await s.flush()
    await audit.record(s, actor=actor, action=action, section="scheduling", target=j.name,
                       after={"agent_id": str(a.id), "agent": a.name, "interval_minutes": interval_minutes,
                              "enabled": enabled})
    return {"name": j.name, "agent_id": str(a.id)}


async def delete_schedule(s: AsyncSession, name: str, *, actor: str) -> None:
    j = (await s.execute(select(ScheduledJob).where(ScheduledJob.name == name,
                                                    ScheduledJob.job_type == AGENT_JOB_TYPE))).scalar_one_or_none()
    if j is None:
        raise NotFound("schedule bulunamadı")
    await s.delete(j)
    await audit.record(s, actor=actor, action="schedule.delete", section="scheduling", target=name,
                       before={"agent_id": (j.params or {}).get("agent_id")})


async def health_check_agent(s: AsyncSession) -> uuid.UUID:
    kv = await s.get(KeyValue, HC_AGENT_KEY)
    try:
        return uuid.UUID((kv.value or {}).get("agent_id")) if kv else SYSTEM_AGENT_ID
    except (TypeError, ValueError):
        return SYSTEM_AGENT_ID


# ------------------------------------------------------------------------------------------------ run kayıtları
def run_out(r: AgentRun, names: dict[str, str] | None = None) -> dict:
    return {"run_id": r.run_id, "agent_id": str(r.agent_id) if r.agent_id else None,
            "agent_name": (names or {}).get(str(r.agent_id)), "agent_key": r.agent_key,
            "agent_version": r.agent_version, "version_label": r.version_label, "mode": r.mode, "trigger": r.trigger,
            "status": r.status, "error_code": r.error_code, "error": r.error, "models": r.models or [],
            "nodes": r.nodes or [], "usage": r.usage or {}, "output": r.output,
            "conversation_id": str(r.conversation_id) if r.conversation_id else None,
            "investigation_id": str(r.investigation_id) if r.investigation_id else None,
            "schedule_name": r.schedule_name, "retry_of": r.retry_of,
            "started_at": r.started_at.isoformat(), "finished_at": r.finished_at.isoformat() if r.finished_at else None,
            "duration_ms": r.duration_ms, "links": {"agent_trace": f"/agent-trace/{r.run_id}",
                                                    "context_windows": f"/context-windows?run_id={r.run_id}",
                                                    "middleware": f"/agent-middleware?tab=timeline&run={r.run_id}"}}


def _visible(q, user):
    from ..db.models import Role
    if user is None or user.role == Role.ADMIN:
        return q
    # sohbet turları yalnız sahibine; zamanlanmış ve test koşuları ekip geneli
    return q.where((AgentRun.mode != "chat") | (AgentRun.user_id == user.id))


async def runs(s: AsyncSession, user, *, agent_id: str | None = None, mode: str | None = None,
               version: str | None = None, status: str | None = None, limit: int = 50, offset: int = 0) -> dict:
    q = _visible(select(AgentRun), user)
    if agent_id:
        q = q.where(AgentRun.agent_id == uuid.UUID(agent_id))
    if mode:
        q = q.where(AgentRun.mode == mode)
    if version:
        q = q.where(AgentRun.version_label == version)
    if status:
        q = q.where(AgentRun.status == status)
    total = (await s.execute(select(func.count()).select_from(q.subquery()))).scalar() or 0
    rows = (await s.execute(q.order_by(AgentRun.started_at.desc()).limit(min(limit, 200)).offset(offset))).scalars()
    names = {str(a.id): a.name for a in (await s.execute(select(Agent))).scalars()}
    return {"total": total, "items": [run_out(r, names) for r in rows]}


async def run(s: AsyncSession, user, run_id: str) -> AgentRun:
    r = (await s.execute(_visible(select(AgentRun).where(AgentRun.run_id == run_id), user))).scalar_one_or_none()
    if r is None:
        raise NotFound("agent run bulunamadı")
    return r


def now() -> datetime:
    return datetime.now(timezone.utc)


async def record_system_run(sm, *, run_id: str, mode: str, trigger: str, status: str, started_at: datetime,
                            user_id=None, conversation_id=None, investigation_id=None, schedule_name: str | None = None,
                            error: str | None = None, models: list | None = None) -> None:
    """Gömülü agent koşusunun özet kaydı (agent listesi 'son çalıştırma', run filtreleri). Hata koşuyu etkilemez."""
    import logging

    from airlab_agent.redact import redact_text

    from ..db.session import transaction
    try:
        end = now()
        inv = uuid.UUID(str(investigation_id)) if investigation_id else None
        async with transaction(sm) as s:
            s.add(AgentRun(run_id=run_id[:160], agent_id=SYSTEM_AGENT_ID, agent_key="sre-agent", version_label="builtin",
                           mode=mode, trigger=trigger, user_id=user_id, conversation_id=conversation_id,
                           investigation_id=inv, schedule_name=schedule_name, status=status, started_at=started_at,
                           finished_at=end, duration_ms=int((end - started_at).total_seconds() * 1000),
                           error=redact_text(error)[:2000] if error else None, models=models or []))
    except Exception:  # noqa: BLE001
        logging.getLogger("sre.builder").debug("sistem run kaydı yazılamadı", exc_info=True)
