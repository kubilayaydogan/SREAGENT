"""Settings API. Yetki sunucu tarafında uygulanır: okuma `sre` (operatör), değişiklik / test / keşif / run-now /
silme `admin`. `viewer` Settings'e erişemez (yalnız /settings/public: görüntü tercihleri). Secret değerleri hiçbir
cevapta yer almaz. Her değişiklik audit'e yazılır; reddedilen değişiklikler de (ayrı transaction'da) kaydedilir.
"""
from __future__ import annotations

import asyncio
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from ...db.models import (AuditEvent, Connector, Conversation, Investigation, InvestigationStatus, JobRun, LlmProfile,
                          Role, ScheduledJob, StoredSecret, User)
from ...db.session import transaction
from ...settings import connectors as conn_svc
from ...settings import llm_profiles as llm_svc
from ...settings import netguard, risk_preview, runtime, secrets, service
from ...settings.schemas import (CONNECTOR_PROVIDERS, LLM_PROVIDERS, SECTIONS, ConnectorIn, ConnectorPatch,
                                 LlmProfileIn, LlmProfilePatch)
from ..deps import admin, current_user, db, sre

router = APIRouter(prefix="/settings", tags=["settings"])

# ------------------------------------------------------------------------------------------------ yardımcılar
_last_test: dict[tuple[str, str], float] = {}
TEST_INTERVAL_S = 5.0


def _rate_limit(user: User, target: str) -> None:
    """Bağlantı testleri sınırlı: aynı kullanıcı + hedef için 5 sn'de bir."""
    k = (str(user.id), target)
    now = time.monotonic()
    if now - _last_test.get(k, 0) < TEST_INTERVAL_S:
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "çok sık test; birkaç saniye sonra tekrar deneyin")
    _last_test[k] = now


async def _audit_separate(request: Request, **kw) -> None:
    """İsteğin transaction'ı geri alınsa da kalıcı olması gereken audit (reddedilen değişiklikler)."""
    try:
        async with transaction(request.app.state.rt.sm) as s:
            await service.audit(s, **kw)
    except Exception:  # noqa: BLE001 — audit yazılamaması isteğin hata cevabını değiştirmez
        pass


def _rt(request: Request):
    return request.app.state.rt


class SectionPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    revision: int = Field(ge=0)
    values: dict[str, Any] = Field(default_factory=dict)
    reset: list[str] = Field(default_factory=list)
    note: str | None = Field(None, max_length=300)


class RollbackIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    revision: int = Field(ge=0)
    to_revision: int = Field(ge=1)


class SecretIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    value: str = Field(min_length=1, max_length=8192)


class ConfirmIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    confirm: str


# ------------------------------------------------------------------------------------------------ bölüm denetimleri
async def _check(rt, s: AsyncSession, section: str, model) -> None:
    """Şemanın ötesindeki doğrulama: referans bütünlüğü ve çalışma zamanı uygunluğu."""
    if section == "llm":
        for task in ("chat_profile", "rca_profile"):
            name = getattr(model, task)
            if name:
                p = await llm_svc.by_name(s, name)
                if p is None:
                    raise service.SettingsInvalid(f"{task}: '{name}' adlı LLM profili yok")
                if not p.enabled:
                    raise service.SettingsInvalid(f"{task}: '{name}' profili devre dışı")
        from ...services.model_switch import MODELS
        if model.model_override and model.model_override not in MODELS:
            raise service.SettingsInvalid(f"model_override desteklenmiyor: {model.model_override}")
    if section == "observability" and model.langsmith_enabled and not rt.agent.langsmith_key:
        raise service.SettingsInvalid("LangSmith açılamaz: LANGSMITH_API_KEY deployment ortamında tanımlı değil")
    if section == "notifications" and model.slack_enabled:
        try:
            url = await secrets.resolve(s, model.slack_webhook_ref)
        except secrets.SecretError as e:
            raise service.SettingsInvalid(f"slack_webhook_ref: {e}") from None
        try:
            await asyncio.to_thread(netguard.check_url, url)
        except netguard.UnsafeUrl as e:
            raise service.SettingsInvalid(f"Slack webhook adresi reddedildi: {e}") from None


async def _after_save(rt, s: AsyncSession, section: str) -> None:
    if section in ("scheduling",):
        await runtime.sync_jobs(rt, s, await service.effective(rt, s))


def _status(rt, section: str, values: dict) -> dict:
    """Bölüme özgü çalışma zamanı durumu (sahte başarı yok: yalnız gerçekten bilinenler)."""
    if section == "agent":
        return {"llm_available": rt.llm is not None,
                "llm_reason": None if rt.llm is not None else "ANTHROPIC_API_KEY tanımlı değil (deterministik mod)",
                "graph": ["plan", "collect", "analyze", "extra_evidence (koşullu)", "explain", "finalize"],
                "backoff": "sınırlı üstel: 1, 2, 4, 8 sn", "concurrency_in_process": rt.sem._value}
    if section == "observability":
        return {"langsmith_key": bool(rt.agent.langsmith_key), "tracer_active": bool(rt.callbacks),
                "restart_required": bool(values["langsmith_enabled"] and not rt.callbacks and rt.agent.langsmith_key),
                "redaction": "Girdi/çıktılar gönderilmeden önce redakte edilir (her zaman açık)",
                "opentelemetry": {"supported": False,
                                  "reason": "OpenTelemetry SDK/exporter bu deployment'ta kurulu değil"}}
    if section == "notifications":
        return {"channels": [
            {"id": "slack", "label": "Slack (Incoming Webhook)", "supported": True},
            {"id": "teams", "label": "Microsoft Teams", "supported": False, "reason": "adapter yok"},
            {"id": "smtp", "label": "E-posta (SMTP)", "supported": False, "reason": "adapter yok"},
            {"id": "webhook", "label": "Genel webhook", "supported": False, "reason": "adapter yok"}],
            "dedup": "investigation başına en fazla bir bildirim", "retries": "3 deneme, 4xx tekrar denenmez"}
    if section == "scheduling":
        return {"cron": {"supported": False, "reason": "cron ifadesi için zamanlayıcı kütüphanesi kurulu değil; "
                                                       "aralık (interval) tabanlı zamanlama kullanılır"},
                "missed_run_policy": "coalesce — kaçırılan periyotlar biriktirilmez, tek koşu yapılır",
                "overlap_policy": "skip — iş kirası (lease) varken ikinci koşu başlamaz; çoklu worker'da "
                                  "FOR UPDATE SKIP LOCKED"}
    if section == "llm":
        return {"fallback": "Açık fallback zinciri yok. Sağlayıcının sunucu tarafı refusal fallback'i açık; "
                            "başka sağlayıcıya geçilmez.",
                "tasks": {"chat": "soru yorumlama, yönlendirme, sohbet cevabı", "rca": "explain (RCA açıklaması)"}}
    return {}


# ------------------------------------------------------------------------------------------------ genel bakış
@router.get("")
async def overview(request: Request, _: User = Depends(sre), s: AsyncSession = Depends(db)):
    rt = _rt(request)
    rows = await service.load_rows(s)
    secs = []
    for sec in SECTIONS:
        v = service.view(rt, sec, rows)
        secs.append({"section": sec, "revision": v["revision"], "updated_by": v["updated_by"],
                     "updated_at": v["updated_at"],
                     "restart_pending": [k for k, f in v["fields"].items() if f["restart_pending"]],
                     "locked": [k for k, f in v["fields"].items() if f["locked"]],
                     "overridden": [k for k, f in v["fields"].items() if f["source"] == "admin"]})
    nconn = await s.scalar(select(func.count()).select_from(Connector))
    nprof = await s.scalar(select(func.count()).select_from(LlmProfile))
    return {"sections": secs, "connectors": nconn, "llm_profiles": nprof,
            "secret_store": secrets.store_available(), "environment": rt.ps.environment}


@router.get("/public")
async def public(request: Request, _: User = Depends(current_user), s: AsyncSession = Depends(db)):
    """Tüm oturumlar: görüntü tercihleri (global). Kullanıcı tema tercihi tarayıcıda ayrı tutulur."""
    g = (await service.effective(_rt(request), s))["general"]
    return {k: g[k] for k in ("display_name", "timezone", "date_format", "language")}


# ------------------------------------------------------------------------------------------------ audit / güvenlik
@router.get("/audit")
async def audit_log(section: str | None = None, page: int = Query(1, ge=1), size: int = Query(50, ge=1, le=200),
                    _: User = Depends(sre), s: AsyncSession = Depends(db)):
    q = select(AuditEvent)
    if section:
        q = q.where(AuditEvent.section == section)
    total = await s.scalar(select(func.count()).select_from(q.subquery()))
    rows = (await s.execute(q.order_by(AuditEvent.ts.desc()).offset((page - 1) * size).limit(size))).scalars()
    items = [{"id": r.id, "ts": r.ts.isoformat(), "actor": r.actor, "action": r.action, "section": r.section,
              "target": r.target, "revision": r.revision, "before": r.before, "after": r.after, "result": r.result,
              "detail": r.detail} for r in rows]
    return {"items": items, "total": total or 0, "page": page, "size": size, "pages": -(-(total or 0) // size)}


@router.get("/security")
async def security(request: Request, _: User = Depends(sre)):
    return {
        "roles": [
            {"role": Role.VIEWER.value, "can": ["Kayıtları görüntüleme", "Görüntü tercihleri"]},
            {"role": Role.SRE.value, "can": ["Investigation başlatma", "Incident / RCA / onay işlemleri",
                                             "Settings ve audit görüntüleme (salt okunur)"]},
            {"role": Role.ADMIN.value, "can": ["Settings değişikliği, rollback", "Connector / LLM profili / secret yönetimi",
                                               "Bağlantı testi, tool keşfi, run-now", "Checkpoint silme"]}],
        "secret_store": {"available": secrets.store_available(),
                         "reason": None if secrets.store_available() else "SRE_SETTINGS_KEY tanımlı değil (≥32 karakter)",
                         "env_allowlist": sorted(secrets.env_allowlist()) + ["SRE_SECRET_*"]},
        "outbound_allowlist": netguard.allowlist_view(),
        "locks": service.locks(),
        "remediation": "Agent yalnız okuma yapar (read-only investigation). Otomatik remediation yoktur; "
                       "aksiyonlar onay akışından geçer.",
        "audit_retention": "Audit kayıtları uygulama tarafından silinmez (deployment politikası).",
    }


# ------------------------------------------------------------------------------------------------ secret'lar
@router.get("/secrets")
async def list_secrets(_: User = Depends(sre), s: AsyncSession = Depends(db)):
    used: dict[str, list[str]] = {}
    for c in (await s.execute(select(Connector))).scalars():
        if c.secret_ref:
            used.setdefault(c.secret_ref, []).append(f"connector:{c.name}")
    for p in (await s.execute(select(LlmProfile))).scalars():
        if p.secret_ref:
            used.setdefault(p.secret_ref, []).append(f"llm:{p.name}")
    rows = await service.load_rows(s)
    ref = (rows["notifications"].data if "notifications" in rows else {}).get("slack_webhook_ref")
    if ref:
        used.setdefault(ref, []).append("notifications:slack")
    stored = [{"ref": f"secret:{r.name}", "name": r.name, "kind": "secret", "hint": r.hint, "created_by": r.created_by,
               "created_at": r.created_at.isoformat(), "rotated_at": r.rotated_at.isoformat() if r.rotated_at else None,
               "used_by": used.get(f"secret:{r.name}", [])}
              for r in (await s.execute(select(StoredSecret).order_by(StoredSecret.name))).scalars()]
    env = [{**secrets.describe(f"env:{n}"), "name": n, "used_by": used.get(f"env:{n}", [])}
           for n in sorted(secrets.env_allowlist())]
    return {"store_available": secrets.store_available(), "stored": stored, "env": env}


@router.put("/secrets/{name}")
async def put_secret(name: str, body: SecretIn, request: Request, u: User = Depends(admin),
                     s: AsyncSession = Depends(db)):
    row, created = await secrets.put(s, name, body.value, u.username)
    await service.audit(s, actor=u.username, action="secret_create" if created else "secret_rotate",
                        section="security", target=f"secret:{name}", after={"hint": row.hint})
    return {"ref": f"secret:{name}", "name": name, "hint": row.hint, "created": created}


@router.delete("/secrets/{name}", status_code=204)
async def delete_secret(name: str, u: User = Depends(admin), s: AsyncSession = Depends(db)):
    row = await s.get(StoredSecret, name)
    if row is None:
        raise HTTPException(404, "secret bulunamadı")
    ref = f"secret:{name}"
    users = [c.name for c in (await s.execute(select(Connector).where(Connector.secret_ref == ref))).scalars()] + \
            [p.name for p in (await s.execute(select(LlmProfile).where(LlmProfile.secret_ref == ref))).scalars()]
    rows = await service.load_rows(s)
    if (rows["notifications"].data if "notifications" in rows else {}).get("slack_webhook_ref") == ref:
        users.append("notifications")
    if users:
        raise HTTPException(409, f"secret kullanımda: {', '.join(users)}; önce referansları değiştirin")
    await s.delete(row)
    await service.audit(s, actor=u.username, action="secret_delete", section="security", target=ref)


# ------------------------------------------------------------------------------------------------ connector'lar
@router.get("/connectors")
async def list_connectors(_: User = Depends(sre), s: AsyncSession = Depends(db)):
    rows = (await s.execute(select(Connector).order_by(Connector.name))).scalars()
    return {"items": [conn_svc.out(c) for c in rows],
            "providers": [{"id": k, **v} for k, v in CONNECTOR_PROVIDERS.items()],
            "transports": [{"id": "streamable_http", "label": "Streamable HTTP", "supported": True},
                           {"id": "sse", "label": "HTTP + SSE (eski)", "supported": False, "reason": "desteklenmiyor"},
                           {"id": "stdio", "label": "stdio", "supported": False,
                            "reason": "arayüzden komut çalıştırılmaz; yalnız deployment tarafından yönetilen sunucular"}],
            "outbound_allowlist": netguard.allowlist_view()}


async def _connector(s: AsyncSession, cid: uuid.UUID, *, lock: bool = False) -> Connector:
    q = select(Connector).where(Connector.id == cid)
    c = (await s.execute(q.with_for_update() if lock else q)).scalar_one_or_none()
    if c is None:
        raise HTTPException(404, "connector bulunamadı")
    return c


def _conn_snapshot(c: Connector) -> dict:
    return {k: v for k, v in conn_svc.out(c).items() if k not in ("tools", "last_check", "updated_at", "secret")} | \
        {"secret_ref": c.secret_ref}


@router.post("/connectors", status_code=201)
async def create_connector(body: ConnectorIn, u: User = Depends(admin), s: AsyncSession = Depends(db)):
    await asyncio.to_thread(netguard.check_url, body.endpoint)
    secrets.check_ref(body.secret_ref)
    if (await s.execute(select(Connector.id).where(Connector.name == body.name))).first():
        raise HTTPException(409, f"'{body.name}' adlı connector zaten var")
    if body.analysis_source and (await conn_svc.analysis_source(s)) is not None:
        raise HTTPException(409, "zaten bir analiz kaynağı var; önce onu kaldırın")
    if body.allowed_tools and body.provider != "dynatrace":
        raise HTTPException(422, "tool onayı keşiften sonra yapılır")
    c = Connector(**body.model_dump(), revision=1)
    if body.provider == "dynatrace" and not body.allowed_tools:
        c.allowed_tools = []
    s.add(c)
    await s.flush()
    if c.allowed_tools:
        conn_svc.validate_tools(c, list(c.allowed_tools))
    await service.audit(s, actor=u.username, action="connector_create", section="connectors", target=c.name,
                        revision=1, after=_conn_snapshot(c))
    return conn_svc.out(c)


@router.patch("/connectors/{cid}")
async def patch_connector(cid: uuid.UUID, body: ConnectorPatch, u: User = Depends(admin),
                          s: AsyncSession = Depends(db)):
    c = await _connector(s, cid, lock=True)
    if body.revision != c.revision:
        raise service.SettingsConflict(c.revision)
    before = _conn_snapshot(c)
    data = body.model_dump(exclude_unset=True, exclude={"revision"})
    secrets.check_ref(data.get("secret_ref"))
    if "endpoint" in data:
        await asyncio.to_thread(netguard.check_url, data["endpoint"])
    merged = ConnectorIn.model_validate({**{k: getattr(c, k) for k in ConnectorIn.model_fields if k != "allowed_tools"},
                                         "allowed_tools": list(c.allowed_tools or []), **data})
    if merged.analysis_source and not c.analysis_source:
        other = await conn_svc.analysis_source(s)
        if other is not None and other.id != c.id:
            raise HTTPException(409, f"analiz kaynağı zaten '{other.name}'; önce onu kaldırın")
    if "allowed_tools" in data:
        conn_svc.validate_tools(c, data["allowed_tools"])
    if merged.read_only and "read_only" in data:
        conn_svc.validate_tools(c, list(merged.allowed_tools))
    for k, v in data.items():
        setattr(c, k, v)
    if "endpoint" in data or "secret_ref" in data or "auth_type" in data:
        c.last_check = {**(c.last_check or {}), "stale": True}
    c.revision += 1
    await s.flush()
    await service.audit(s, actor=u.username, action="connector_update", section="connectors", target=c.name,
                        revision=c.revision, before=before, after=_conn_snapshot(c))
    return conn_svc.out(c)


@router.delete("/connectors/{cid}", status_code=204)
async def delete_connector(cid: uuid.UUID, u: User = Depends(admin), s: AsyncSession = Depends(db)):
    c = await _connector(s, cid, lock=True)
    if c.analysis_source:
        raise HTTPException(409, "analiz kaynağı silinemez; önce başka bir connector'ı kaynak yapın ya da devre dışı bırakın")
    before = _conn_snapshot(c)
    await s.delete(c)
    await service.audit(s, actor=u.username, action="connector_delete", section="connectors", target=before["name"],
                        before=before)


@router.post("/connectors/{cid}/test")
async def test_connector(cid: uuid.UUID, u: User = Depends(admin), s: AsyncSession = Depends(db)):
    _rate_limit(u, f"conn:{cid}")
    c = await _connector(s, cid)
    res = await conn_svc.test(s, c)
    await service.audit(s, actor=u.username, action="connector_test", section="connectors", target=c.name,
                        result="ok" if res["ok"] else "error", detail=res.get("message"))
    return res


@router.post("/connectors/{cid}/discover")
async def discover_connector(cid: uuid.UUID, u: User = Depends(admin), s: AsyncSession = Depends(db)):
    _rate_limit(u, f"disc:{cid}")
    c = await _connector(s, cid)
    res = await conn_svc.discover(s, c)
    await service.audit(s, actor=u.username, action="connector_discover", section="connectors", target=c.name,
                        result="ok" if res["ok"] else "error",
                        detail=f"{len(res.get('tools', []))} tool" if res["ok"] else res.get("message"))
    return res


# ------------------------------------------------------------------------------------------------ LLM profilleri
@router.get("/llm-providers")
async def list_profiles(request: Request, _: User = Depends(sre), s: AsyncSession = Depends(db)):
    out = []
    for p in (await s.execute(select(LlmProfile).order_by(LlmProfile.name))).scalars():
        out.append(llm_svc.out(p, await llm_svc.month_cost(s, p.model)))
    eff = (await service.effective(_rt(request), s))["llm"]
    return {"items": out, "providers": [{"id": k, **v} for k, v in LLM_PROVIDERS.items()],
            "routing": {"chat": eff["chat_profile"], "rca": eff["rca_profile"]},
            "cost_source": "LangSmith senkronu (sre.llm_runs)" if _rt(request).agent.langsmith_key else None}


async def _profile(s: AsyncSession, pid: uuid.UUID, *, lock: bool = False) -> LlmProfile:
    q = select(LlmProfile).where(LlmProfile.id == pid)
    p = (await s.execute(q.with_for_update() if lock else q)).scalar_one_or_none()
    if p is None:
        raise HTTPException(404, "LLM profili bulunamadı")
    return p


def _prof_snapshot(p: LlmProfile) -> dict:
    d = llm_svc.out(p)
    return {k: v for k, v in d.items() if k not in ("last_check", "updated_at", "secret", "month_cost_usd", "verified")} | \
        {"secret_ref": p.secret_ref}


@router.post("/llm-providers", status_code=201)
async def create_profile(body: LlmProfileIn, u: User = Depends(admin), s: AsyncSession = Depends(db)):
    secrets.check_ref(body.secret_ref)
    if body.base_url:
        await asyncio.to_thread(netguard.check_url, body.base_url)
    if (await s.execute(select(LlmProfile.id).where(LlmProfile.name == body.name))).first():
        raise HTTPException(409, f"'{body.name}' adlı profil zaten var")
    p = LlmProfile(**body.model_dump(), revision=1)
    s.add(p)
    await s.flush()
    await service.audit(s, actor=u.username, action="llm_profile_create", section="llm", target=p.name, revision=1,
                        after=_prof_snapshot(p))
    return llm_svc.out(p)


async def _routed(request: Request, s: AsyncSession, name: str) -> list[str]:
    eff = (await service.effective(_rt(request), s))["llm"]
    return [t for t in ("chat", "rca") if eff[f"{t}_profile"] == name]


@router.patch("/llm-providers/{pid}")
async def patch_profile(pid: uuid.UUID, body: LlmProfilePatch, request: Request, u: User = Depends(admin),
                        s: AsyncSession = Depends(db)):
    p = await _profile(s, pid, lock=True)
    if body.revision != p.revision:
        raise service.SettingsConflict(p.revision)
    data = body.model_dump(exclude_unset=True, exclude={"revision"})
    secrets.check_ref(data.get("secret_ref"))
    if data.get("base_url"):
        await asyncio.to_thread(netguard.check_url, data["base_url"])
    if data.get("enabled") is False and (tasks := await _routed(request, s, p.name)):
        raise HTTPException(409, f"profil şu görevlerde kullanılıyor: {', '.join(tasks)}; önce başka profile yönlendirin")
    if "monthly_budget_usd" in data and data["monthly_budget_usd"] == 0:
        data["monthly_budget_usd"] = None
    before = _prof_snapshot(p)
    LlmProfileIn.model_validate({**{k: getattr(p, k) for k in LlmProfileIn.model_fields
                                    if k not in ("monthly_budget_usd",)},
                                 "monthly_budget_usd": float(p.monthly_budget_usd) if p.monthly_budget_usd else None,
                                 **data})
    for k, v in data.items():
        setattr(p, k, v)
    if {"model", "secret_ref", "base_url"} & set(data):
        p.last_check = {**(p.last_check or {}), "ok": False, "stale": True}
    p.revision += 1
    await s.flush()
    await service.audit(s, actor=u.username, action="llm_profile_update", section="llm", target=p.name,
                        revision=p.revision, before=before, after=_prof_snapshot(p))
    return llm_svc.out(p)


@router.delete("/llm-providers/{pid}", status_code=204)
async def delete_profile(pid: uuid.UUID, request: Request, u: User = Depends(admin), s: AsyncSession = Depends(db)):
    p = await _profile(s, pid, lock=True)
    if tasks := await _routed(request, s, p.name):
        raise HTTPException(409, f"varsayılan profil silinemez ({', '.join(tasks)}); önce başka profile yönlendirin")
    before = _prof_snapshot(p)
    await s.delete(p)
    await service.audit(s, actor=u.username, action="llm_profile_delete", section="llm", target=before["name"],
                        before=before)


@router.post("/llm-providers/{pid}/test")
async def test_profile(pid: uuid.UUID, u: User = Depends(admin), s: AsyncSession = Depends(db)):
    _rate_limit(u, f"llm:{pid}")
    p = await _profile(s, pid)
    res = await llm_svc.test(s, p)
    await service.audit(s, actor=u.username, action="llm_profile_test", section="llm", target=p.name,
                        result="ok" if res["ok"] else "error", detail=res.get("message"))
    return res


@router.get("/llm-providers/{pid}/models")
async def profile_models(pid: uuid.UUID, u: User = Depends(admin), s: AsyncSession = Depends(db)):
    _rate_limit(u, f"llm-models:{pid}")
    p = await _profile(s, pid)
    try:
        return {"items": await llm_svc.list_models(s, p)}
    except (secrets.SecretError, netguard.UnsafeUrl):
        raise
    except Exception as e:  # noqa: BLE001
        kind, msg = llm_svc._err(e, None)
        raise HTTPException(502, f"model listesi alınamadı ({kind}): {msg}") from None


# ------------------------------------------------------------------------------------------------ test eylemleri
@router.post("/observability/test")
async def test_observability(request: Request, u: User = Depends(admin), s: AsyncSession = Depends(db)):
    """LangSmith erişimi: /info uç noktası (gerçek evidence gönderilmez)."""
    _rate_limit(u, "langsmith")
    from airlab_agent.tracing import langsmith_ping
    ok, detail = await asyncio.to_thread(langsmith_ping, _rt(request).agent)
    await service.audit(s, actor=u.username, action="observability_test", section="observability",
                        result="ok" if ok else "error", detail=detail)
    return {"ok": ok, "detail": detail, "sent_data": "yok (yalnız /info query)"}


@router.post("/notifications/test")
async def test_notification(request: Request, u: User = Depends(admin), s: AsyncSession = Depends(db)):
    """Açıkça TEST etiketli Slack mesajı. Evidence/secret içermez."""
    _rate_limit(u, "slack")
    eff = (await service.effective(_rt(request), s))["notifications"]
    if not eff["slack_webhook_ref"]:
        raise HTTPException(409, "Slack webhook secret referansı tanımlı değil")
    url = await secrets.resolve(s, eff["slack_webhook_ref"])
    await asyncio.to_thread(netguard.check_url, url)
    from ...services.slack_notify import _post
    payload = {"text": f"[TEST] airlab SRE Platform bildirim testi — gerçek bir olay değildir. "
                       f"Gönderen: {u.username}, {datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC"}
    try:
        await _post(url, payload)
        ok, detail = True, "gönderildi"
    except Exception as e:  # noqa: BLE001
        from airlab_agent.redact import redact_text
        ok, detail = False, redact_text(str(e)).replace(url, "<webhook>")[:300]
    await service.audit(s, actor=u.username, action="notification_test", section="notifications",
                        result="ok" if ok else "error", detail=detail)
    return {"ok": ok, "detail": detail}


# ------------------------------------------------------------------------------------------------ zamanlama
@router.get("/scheduling/jobs")
async def jobs_status(_: User = Depends(sre), s: AsyncSession = Depends(db)):
    out = []
    for j in (await s.execute(select(ScheduledJob).order_by(ScheduledJob.name))).scalars():
        last = (await s.execute(select(JobRun).where(JobRun.job_id == j.id).order_by(JobRun.started_at.desc())
                                .limit(1))).scalar_one_or_none()
        nxt = [j.next_run_at + timedelta(seconds=j.interval_seconds * i) for i in range(3)] if j.enabled else []
        running = bool(j.locked_until and j.locked_until > datetime.now(timezone.utc))
        out.append({"name": j.name, "type": j.job_type, "enabled": j.enabled, "interval_seconds": j.interval_seconds,
                    "next_runs": [t.isoformat() for t in nxt], "running": running, "locked_by": j.locked_by if running else None,
                    "last_run": {"status": last.status.value, "started_at": last.started_at.isoformat(),
                                 "finished_at": last.finished_at.isoformat() if last.finished_at else None,
                                 "error": last.error} if last else None})
    return {"items": out}


@router.post("/scheduling/jobs/{name}/run")
async def run_now(name: str, request: Request, u: User = Depends(admin), s: AsyncSession = Depends(db)):
    """Bir sonraki worker turunda çalıştır. Kapalı ya da şu an çalışan iş için 409 (çift koşu yok)."""
    eff = await service.effective(_rt(request), s)
    if name == "health-check":
        try:
            runtime.check_allowed(eff, "scheduled")
        except runtime.RunNotAllowed as e:
            raise HTTPException(409, str(e)) from None
    res = await s.execute(text("""UPDATE sre.scheduled_jobs SET next_run_at = now(), updated_at = now()
        WHERE name = :n AND enabled AND (locked_until IS NULL OR locked_until < now()) RETURNING id"""), {"n": name})
    if res.first() is None:
        exists = await s.scalar(select(ScheduledJob.enabled).where(ScheduledJob.name == name))
        await _audit_separate(request, actor=u.username, action="job_run_now", section="scheduling", target=name,
                              result="rejected", detail="iş yok, kapalı ya da çalışıyor")
        raise HTTPException(404 if exists is None else 409,
                            "iş bulunamadı" if exists is None else "iş kapalı ya da şu an çalışıyor")
    await service.audit(s, actor=u.username, action="job_run_now", section="scheduling", target=name)
    return {"queued": True, "note": "worker bir sonraki turunda (≤5 sn) başlatır"}


# ------------------------------------------------------------------------------------------------ bellek
@router.get("/memory")
async def memory_status(request: Request, _: User = Depends(sre), s: AsyncSession = Depends(db)):
    rt = _rt(request)
    counts = (await s.execute(text("""SELECT
        (SELECT count(DISTINCT thread_id) FROM langgraph.checkpoints),
        (SELECT count(*) FROM langgraph.checkpoints),
        (SELECT count(*) FROM langgraph.store),
        pg_total_relation_size('langgraph.checkpoints') + pg_total_relation_size('langgraph.checkpoint_blobs')
          + pg_total_relation_size('langgraph.checkpoint_writes'),
        pg_total_relation_size('langgraph.store')"""))).one()
    active_inv = [r[0] for r in await s.execute(select(Investigation.run_id).where(
        Investigation.status.in_([InvestigationStatus.PENDING, InvestigationStatus.RUNNING])))]
    eff = (await service.effective(rt, s))["retention"]
    return {
        "checkpointer": {"backend": "PostgresSaver", "durable": True, "schema": "langgraph",
                         "scope": "thread başına: investigation run_id, sohbet conversation.thread_id",
                         "threads": counts[0], "checkpoints": counts[1], "bytes": counts[3]},
        "store": {"backend": "AsyncPostgresStore", "durable": True, "schema": "langgraph",
                  "scope": "kullanıcı / uygulama / mechanism namespace'leri", "items": counts[2], "bytes": counts[4]},
        "supported_backends": [
            {"id": "postgres", "label": "PostgresSaver / PostgresStore", "status": "aktif",
             "note": "çok worker'lı kalıcı deployment"},
            {"id": "sqlite", "label": "SQLite", "status": "desteklenmiyor",
             "note": "yalnız yerel tek süreçli geliştirme içindir; bu platformda backend ve worker ayrı süreç"},
            {"id": "memory", "label": "InMemorySaver", "status": "desteklenmiyor",
             "note": "yeniden başlatmada kaybolur; yarım kalan tur sürdürülemez"}],
        "switching": "Backend değişimi canlı yapılamaz: veri taşıma + yeniden başlatma gerekir. Mevcut durum sessizce "
                     "silinmez.",
        "note": "Checkpoint uzun vadeli hafıza değildir: yalnız yarım kalan koşu/turun sürdürülmesi içindir.",
        "active_threads": active_inv,
        "retention": {"checkpoints": "otomatik silinmez; aşağıdan thread bazında silinebilir",
                      "investigations_days": eff["investigations_days"]},
    }


@router.post("/memory/threads/{thread_id}/delete")
async def delete_thread(thread_id: str, body: ConfirmIn, request: Request, u: User = Depends(admin),
                        s: AsyncSession = Depends(db)):
    """Tek thread'in checkpoint'lerini siler. Onay metni thread kimliğiyle aynı olmalı; aktif koşu silinemez."""
    if body.confirm != thread_id:
        raise HTTPException(422, "onay için thread kimliğini aynen yazın")
    inv = (await s.execute(select(Investigation).where(Investigation.run_id == thread_id))).scalar_one_or_none()
    conv = (await s.execute(select(Conversation).where(Conversation.thread_id == thread_id))).scalar_one_or_none()
    if inv is None and conv is None:
        raise HTTPException(404, "bu kimlikte bir investigation ya da konuşma thread'i yok")
    if inv is not None and inv.status in (InvestigationStatus.PENDING, InvestigationStatus.RUNNING):
        raise HTTPException(409, "aktif investigation'ın checkpoint'leri silinemez")
    if conv is not None and request.app.state.runs.active_for_conversation(conv.id):
        raise HTTPException(409, "bu konuşmada süren bir tur var; checkpoint silinemez")
    rt = _rt(request)
    n = await s.scalar(text("SELECT count(*) FROM langgraph.checkpoints WHERE thread_id = :t"), {"t": thread_id})
    await rt.asaver.adelete_thread(thread_id)
    await service.audit(s, actor=u.username, action="checkpoint_delete", section="memory", target=thread_id,
                        detail=f"{n or 0} checkpoint silindi")
    return {"deleted_checkpoints": n or 0}


# ------------------------------------------------------------------------------------------------ bölümler
@router.post("/risk/preview")
async def risk_preview_ep(request: Request, values: dict[str, Any] = Body(default_factory=dict),
                          _: User = Depends(sre), s: AsyncSession = Depends(db)):
    rt = _rt(request)
    cur = (await service.effective(rt, s))["risk"]
    try:
        draft = SECTIONS["risk"].model_validate({**cur, **values}).model_dump()
    except Exception as e:  # noqa: BLE001
        raise service.SettingsInvalid(str(e).split("\n")[1] if "\n" in str(e) else str(e)) from None
    return risk_preview.preview(rt.agent.cfg, cur, draft)


def _section(section: str) -> str:
    if section not in SECTIONS:
        raise HTTPException(404, f"bilinmeyen ayar bölümü: {section}")
    return section


@router.get("/{section}")
async def get_section(section: str, request: Request, _: User = Depends(sre), s: AsyncSession = Depends(db)):
    rt = _rt(request)
    v = service.view(rt, _section(section), await service.load_rows(s))
    return {**v, "status": _status(rt, section, v["values"])}


@router.patch("/{section}")
async def patch_section(section: str, body: SectionPatch, request: Request, u: User = Depends(admin),
                        s: AsyncSession = Depends(db)):
    rt = _rt(request)
    _section(section)

    async def check(model):
        await _check(rt, s, section, model)
    try:
        v = await service.update(rt, s, section, revision=body.revision, values=body.values, reset=body.reset,
                                 actor=u.username, note=body.note, check=check)
    except service.SettingsInvalid as e:
        await _audit_separate(request, actor=u.username, action="update", section=section, result="rejected",
                              after=body.values, detail=str(e))
        raise
    await _after_save(rt, s, section)
    return {**v, "status": _status(rt, section, v["values"])}


@router.get("/{section}/revisions")
async def section_revisions(section: str, _: User = Depends(sre), s: AsyncSession = Depends(db)):
    return {"items": await service.revisions(s, _section(section))}


@router.post("/{section}/rollback")
async def section_rollback(section: str, body: RollbackIn, request: Request, u: User = Depends(admin),
                           s: AsyncSession = Depends(db)):
    rt = _rt(request)
    _section(section)

    async def check(model):
        await _check(rt, s, section, model)
    v = await service.rollback(rt, s, section, to_revision=body.to_revision, revision=body.revision,
                               actor=u.username, check=check)
    await _after_save(rt, s, section)
    return {**v, "status": _status(rt, section, v["values"])}
