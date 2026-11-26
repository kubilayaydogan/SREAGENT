"""Settings → çalışma zamanı köprüsü.

Her investigation koşusu başında `run_config` etkili ayarlardan DEĞİŞMEZ bir yapılandırma kurar:
  * agent Settings'in derin kopyası (windows / budgets / score cfg'si birleştirilmiş) — global durum değiştirilmez
  * zaman aşımı, recursion_limit, gateway fabrikası (analiz kaynağı connector'ı), LLM görev profilleri, tracing kararı
  * snapshot: bölüm revizyonları + etkili değerler + connector/profil revizyonları (secret YOK) → investigations.config_snapshot
Koşu sürerken yapılan ayar değişiklikleri o koşuyu etkilemez; bir sonraki koşudan itibaren geçerlidir.

"restart" alanları (max_concurrent_runs, LangSmith project, watch eşikleri) süreç açılışında `apply_startup` ile uygulanır.
"""
from __future__ import annotations

import asyncio
import copy
import dataclasses
import hashlib
import json
import logging
import random
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models import ScheduledJob
from ..db.session import transaction
from . import connectors, llm_profiles, service

log = logging.getLogger("sre.settings.runtime")


class RunNotAllowed(Exception):
    """Policy bu tetikleyiciyle yeni koşuya izin vermiyor (agent kapalı / execution mode). Kararı taşır."""

    def __init__(self, message: str, decision=None):
        super().__init__(message)
        self.decision = decision
        self.reason_code = getattr(decision, "reason_code", None) or "policy_agent_settings"


@dataclass
class RunConfig:
    agent: Any
    timeout_s: int
    recursion_limit: int
    gateway_factory: Any
    llm_chat: bool
    llm_rca: bool
    profiles: dict[str, dict | None]
    model_override: str | None
    llm_effective: dict
    trace: bool
    snapshot: dict
    effective: dict
    notes: list[str] = field(default_factory=list)
    middleware: dict = field(default_factory=dict)        # Agent Middleware yapılandırma snapshot'ı (run boyunca sabit)


def merged_cfg(base_cfg: dict, eff: dict) -> dict:
    cfg = copy.deepcopy(base_cfg)
    a, r = eff["agent"], eff["risk"]
    cfg["windows"].update(default_minutes=a["analysis_window_minutes"], baseline_minutes=a["baseline_minutes"],
                          max_minutes=a["max_window_minutes"])
    cfg["budgets"].update(max_iterations=a["max_extra_iterations"], max_queries_per_run=a["max_queries_per_run"],
                          query_timeout_seconds=a["query_timeout_s"], query_retries=a["query_retries"],
                          run_timeout_seconds=a["run_timeout_s"])
    cfg["score"].update(weights=dict(r["weights"]), bands=dict(r["bands"]), **{k: list(v) for k, v in r["ramps"].items()})
    cfg["quality"]["min_score_coverage"] = r["min_score_coverage"]
    cfg["watch"].update(llm_on_incident=a["incident_llm"])
    return cfg


def _hash(d: dict) -> str:
    return hashlib.sha256(json.dumps(d, sort_keys=True, default=str).encode()).hexdigest()[:16]


def check_allowed(eff: dict, trigger: str):
    """trigger: manual (sohbet / manuel investigation / resume) | scheduled (health check) | incident (watch).
    Karar merkezi policy'dedir (security/policy.py); ALLOW değilse RunNotAllowed. ALLOW kararını döndürür."""
    from ..security import policy
    d = policy.decide("run.start", {"trigger": trigger, "agent": eff["agent"]})
    policy.log_decision(d, trigger=trigger)
    if not d.allowed:
        raise RunNotAllowed(d.message, d)
    return d


async def effective(rt) -> dict:
    async with transaction(rt.sm) as s:
        return await service.effective(rt, s)


async def run_config(rt, *, trigger: str = "manual") -> RunConfig:
    async with transaction(rt.sm) as s:
        rows = await service.load_rows(s)
        eff = service.effective_from(rt, rows)
        agent = dataclasses.replace(rt.agent, cfg=merged_cfg(rt.agent.cfg, eff))
        if getattr(rt.gateway_factory, "_settings_default", False):
            gw, gw_info = await connectors.gateway_factory(s, agent)
        else:                                     # test/özel enjeksiyon: verilen fabrika aynen kullanılır
            gw, gw_info = rt.gateway_factory, {"connector": "injected"}
        llm_eff = eff["llm"]
        allowed = llm_profiles.allowed_models(rt)
        profiles, prof_snap, notes = await llm_profiles.task_profiles(s, llm_eff, allowed=allowed)
        override = await _checked_override(s, llm_eff.get("model_override"), allowed, notes)
        from ..middleware import service as mw_service
        mw_snap = await mw_service.snapshot(s)
    from airlab_agent import middleware as _mw
    _mw.set_fallback_config(mw_snap)                # run dışı çağrılar (watch) en son yüklenen yapılandırmayı kullanır
    a, o = eff["agent"], eff["observability"]
    has_llm = rt.llm is not None and a["llm_enabled"]
    llm_chat = has_llm and profiles.get("chat", True) is not None
    llm_rca = has_llm and profiles.get("rca", True) is not None
    trace = bool(rt.callbacks) and o["langsmith_enabled"] and random.random() < float(o["sampling_rate"])
    snapshot = {
        "revisions": {sec: rows[sec].revision if sec in rows else 0 for sec in service.SECTIONS},
        "trigger": trigger,
        "agent": {k: a[k] for k in ("analysis_window_minutes", "baseline_minutes", "run_timeout_s",
                                    "max_extra_iterations", "recursion_limit", "max_queries_per_run",
                                    "query_timeout_s", "query_retries", "llm_enabled", "execution_mode")},
        "risk": eff["risk"], "connector": gw_info,
        "llm": {"enabled": has_llm, "chat": prof_snap.get("chat"), "rca": prof_snap.get("rca"),
                "model_override": llm_eff.get("model_override")},
        "tracing": {"langsmith": trace, "sampling_rate": o["sampling_rate"]},
        "memory": eff.get("agent_memory", {}),
        "middleware": {n: {"version": c["version"], "enabled": c["enabled"], "order": c["order"]}
                       for n, c in mw_snap.items()},
    }
    snapshot["hash"] = _hash({k: v for k, v in snapshot.items() if k != "trigger"})
    # Etkili model + seçim kaynağı (hash'e girmez: aynı girdilerden türetilir, eski koşularla hash karşılaştırılabilir kalır)
    llm_effective = effective_models(rt, has_llm=has_llm, profiles=profiles, prof_snap=prof_snap, override=override,
                                     llm_enabled=bool(a["llm_enabled"]))
    snapshot["llm"]["effective"] = llm_effective
    if llm_eff.get("model_override") and not override:
        snapshot["llm"]["model_override_ignored"] = "model_not_allowlisted"
    return RunConfig(agent=agent, timeout_s=int(a["run_timeout_s"]), recursion_limit=int(a["recursion_limit"]),
                     gateway_factory=gw, llm_chat=llm_chat, llm_rca=llm_rca,
                     profiles={k: v for k, v in profiles.items() if v}, model_override=override,
                     llm_effective=llm_effective, trace=trace, snapshot=snapshot, effective=eff, notes=notes, middleware=mw_snap)


async def _checked_override(s, override: str | None, allowed: frozenset[str], notes: list[str]) -> str | None:
    """Üst bardaki model seçimi de model allowlist'ine tabidir (LLM profilleriyle aynı kural). İzinli değilse seçim
    yok sayılır: görev profili / ortam modeli geçerli olur, karar nota ve audit'e yazılır."""
    from ..services.model_switch import MODELS
    if not override or override not in MODELS:
        return None
    if override in allowed:
        return override
    from ..security import audit
    await audit.record(s, actor="system:agent", action="llm.model_denied", section="llm", target="model_override",
                       result="denied", reason_code="model_not_allowlisted", detail=override)
    notes.append(f"Üst bardaki model seçimi ({override}) izinli model listesinde değil; LLM profili / ortam modeli "
                 f"kullanıldı")
    return None


def effective_models(rt, *, has_llm: bool, profiles: dict, prof_snap: dict, override: str | None,
                     llm_enabled: bool) -> dict:
    """Görev başına (chat, rca) bu koşuda geçerli model ve seçimin kaynağı. Öncelik: üst bar seçimi (override) >
    görev LLM profili > ortam (CLAUDE_MODEL / CLAUDE_RCA_MODEL). `model: None` = bu görevde LLM yok (+ `reason`)."""
    env = {"chat": getattr(rt.agent, "claude_model", None), "rca": getattr(rt.agent, "claude_rca_model", None)}
    out = {}
    for task in ("chat", "rca"):
        snap = prof_snap.get(task) or {}
        if rt.llm is None:
            out[task] = {"model": None, "source": "none", "reason": "llm_not_configured"}
        elif not llm_enabled:
            out[task] = {"model": None, "source": "none", "reason": "llm_disabled"}
        elif task in profiles and profiles[task] is None:
            why = next((k for k in ("disabled", "model_denied", "budget_exhausted", "secret_error", "egress_denied")
                        if snap.get(k)), "profile_unavailable")
            out[task] = {"model": None, "source": "none", "reason": f"profile_{why}" if not why.startswith("profile")
                         else why, "profile": snap.get("profile")}
        elif override:
            out[task] = {"model": override, "source": "override", "profile": snap.get("profile")}
        elif snap.get("model"):
            out[task] = {"model": snap["model"], "source": "profile", "profile": snap.get("profile")}
        else:
            out[task] = {"model": env[task], "source": "env"}
    return out


async def preview_models(rt, s: AsyncSession) -> dict:
    """Bir sonraki gömülü agent koşusunda geçerli olacak modeller (üst bar ve Settings görünümü için). Secret
    çözülmez, audit yazılmaz; yalnız kayıtlı seçim, profil durumu ve allowlist okunur."""
    eff = await service.effective(rt, s)
    llm_eff, allowed = eff["llm"], llm_profiles.allowed_models(rt)
    from ..services.model_switch import MODELS
    override = llm_eff.get("model_override") or None
    selected = override if override in MODELS and override in allowed else None
    profiles, snap = {}, {}
    for task in llm_profiles.TASKS:
        name = llm_eff.get(f"{task}_profile")
        if not name:
            continue
        p = await llm_profiles.by_name(s, name)
        if p is None or not p.enabled:
            profiles[task], snap[task] = None, {"profile": name, "disabled": True}
        elif p.model not in allowed:
            profiles[task], snap[task] = None, {"profile": name, "model_denied": True}
        else:
            profiles[task], snap[task] = {"model": p.model}, {"profile": name, "model": p.model}
    out = effective_models(rt, has_llm=rt.llm is not None, profiles=profiles, prof_snap=snap, override=selected,
                           llm_enabled=bool(eff["agent"]["llm_enabled"]))
    return {**out, "selected": selected, "ignored": override if override and not selected else None}


RUN_LLM_REASON = {"manual": "run_choice", "scheduled": "scheduled_llm_off", "watch-incident": "incident_llm_off",
                  "watch-recovery": "incident_llm_off"}


def llm_run(rc: RunConfig, *, mode: str, requested: bool, used: bool) -> dict:
    """Koşunun analiz açıklaması (explain) için LLM kullanıp kullanmadığı ve kullanmadıysa nedeni. Sohbet turunda
    açıklama yanıt adımında (respond) üretilir: `stage: answer`."""
    e = rc.llm_effective.get("rca") or {}
    if mode == "chat":
        return {"stage": "answer", "planned": bool(e.get("model")), "model": e.get("model"), "source": e.get("source"),
                **({"reason": e["reason"]} if e.get("reason") else {})}
    if used:
        return {"stage": "explain", "used": True, "model": e.get("model"), "source": e.get("source")}
    return {"stage": "explain", "used": False, "model": None,
            "reason": e.get("reason") or (RUN_LLM_REASON.get(mode, "not_requested") if not requested else "unavailable")}


def models_view(llm_effective: dict | None, tasks=("chat",)) -> list[dict]:
    """Run kaydı (agent_runs.models) için: bu koşuda geçerli model + seçim kaynağı. LLM'siz görev listeye girmez."""
    out = []
    for t in tasks:
        e = (llm_effective or {}).get(t) or {}
        if e.get("model"):
            out.append({"node_id": t, "provider": "anthropic", "model": e["model"], "profile": e.get("profile"),
                        "source": e.get("source")})
    return out


def apply_llm(rt, rc: RunConfig) -> None:
    """Görev profillerini ve model seçimini paylaşılan istemciye uygular (sonraki çağrılardan itibaren)."""
    # P1.6: koşu başına LLM bütçesi bu task'ın context'ine bağlanır (LLM kapalı olsa da kayıt tutarlı olsun)
    from airlab_agent.llm import RUN_BUDGET, RunBudget
    a = rc.effective["agent"]
    RUN_BUDGET.set(RunBudget(a.get("llm_max_calls_per_run", 12), a.get("llm_max_tokens_per_run", 400_000)))
    core = getattr(rt.llm, "llm", None) if rt.llm is not None else None
    if core is None:
        return
    if hasattr(core, "set_profiles"):
        core.set_profiles(rc.profiles)
    if hasattr(core, "set_model"):
        from ..services.model_switch import MODELS
        core.set_model(rc.model_override if rc.model_override in MODELS else None)


# ------------------------------------------------------------------------------------------------ açılış
async def apply_startup(rt) -> None:
    """Bootstrap'ta: tohumlama, eski model seçimini taşıma ve 'restart' alanlarını uygulama. Koşu yokken çalışır."""
    from ..services import model_switch
    async with transaction(rt.sm) as s:
        await connectors.seed(s, rt)
        await llm_profiles.seed(s, rt)
        await model_switch.migrate_legacy(rt, s)
        eff = await service.effective(rt, s)
    a, o = eff["agent"], eff["observability"]
    if a["max_concurrent_runs"] != rt.ps.max_concurrent_investigations:
        rt.sem = asyncio.Semaphore(int(a["max_concurrent_runs"]))
    rt.agent.cfg["watch"].update(confirm_windows=a["watch_confirm_windows"], cooldown_minutes=a["watch_cooldown_minutes"],
                                 poll_seconds=eff["scheduling"]["watch_poll_seconds"])
    _apply_tracing(rt, o)
    rt.settings_started = eff


def _apply_tracing(rt, o: dict) -> None:
    ag = rt.agent
    core = getattr(rt.llm, "llm", None) if rt.llm is not None else None
    if not ag.langsmith_key:
        rt.callbacks = []
    elif o["langsmith_enabled"] and (not rt.callbacks or o["langsmith_project"] != ag.langsmith_project):
        from airlab_agent.tracing import setup_tracing
        ag.langsmith_project = o["langsmith_project"]
        rt.callbacks = setup_tracing(dataclasses.replace(ag, langsmith_tracing=True))
    elif not o["langsmith_enabled"]:
        rt.callbacks = []
    if core is not None:
        core.callbacks = list(rt.callbacks)


# ------------------------------------------------------------------------------------------------ zamanlayıcı
JOBS = {"health-check", "watch", "langsmith-sync", "retention"}


def job_specs(rt, eff: dict) -> dict[str, dict]:
    sch = eff["scheduling"]
    return {
        "health-check": {"interval_seconds": int(sch["health_check_interval_minutes"]) * 60,
                         "params": {"window_minutes": int(sch["health_check_window_minutes"])},
                         "enabled": bool(sch["health_check_enabled"])},
        "watch": {"interval_seconds": int(sch["watch_poll_seconds"]), "params": {},
                  "enabled": bool(sch["watch_enabled"])},
        "langsmith-sync": {"interval_seconds": int(sch["langsmith_sync_minutes"]) * 60,
                           "params": {"backfill_days": rt.ps.langsmith_backfill_days},
                           "enabled": bool(sch["langsmith_sync_enabled"]) and bool(rt.agent.langsmith_key)},
    }


async def sync_jobs(rt, s: AsyncSession, eff: dict) -> None:
    """Zamanlama tablosunu etkili ayarlara getirir. Aralık kısaldıysa bir sonraki çalışma öne çekilir;
    kapatılan iş yeni koşu başlatmaz, süren koşu (kira) durdurulmaz."""
    from sqlalchemy import func as f
    for name, spec in job_specs(rt, eff).items():
        soonest = f.now() + f.make_interval(0, 0, 0, 0, 0, 0, spec["interval_seconds"])
        await s.execute(update(ScheduledJob).where(ScheduledJob.name == name).values(
            interval_seconds=spec["interval_seconds"], params=spec["params"], enabled=spec["enabled"],
            next_run_at=f.least(ScheduledJob.next_run_at, soonest)))
