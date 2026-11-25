"""Settings servisi: etkili yapılandırma, kaynak gösterimi, revizyonlu atomik güncelleme, rollback, audit.

Öncelik (yüksekten düşüğe):
  1. deployment kilidi   SRE_SETTINGS_LOCKS="agent.max_concurrent_runs=2;observability.langsmith_enabled=false"
  2. yönetici ayarı      sre.settings_sections.data (yalnız bootstrap'tan FARKLI alanlar saklanır)
  3. ortam değişkeni     mevcut SRE_* / LANGSMITH_* / AGENT_CONFIG değerleri (bootstrap)
  4. uygulama varsayılanı defaults.yaml + PlatformSettings varsayılanları

Bir alan "varsayılana dön" ile sıfırlanırsa yönetici kaydından çıkar ve yeniden ortam/varsayılan değerini izler.
Kaydedilen her değişiklik yeni bir revizyondur (settings_revisions); rollback eski revizyonun verisini YENİ revizyon
olarak yazar. Secret değerleri bu katmanda yoktur.
"""
from __future__ import annotations

import copy
import json
import logging
import os
from datetime import datetime, timezone
from importlib import resources
from typing import Any

import yaml
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from airlab_agent.redact import redact

from ..db.models import AuditEvent, SettingsRevision, SettingsSection
from .schemas import SECTIONS, field_meta

log = logging.getLogger("sre.settings")


class SettingsConflict(Exception):
    """Revizyon uyuşmazlığı (başka biri kaydetti)."""

    def __init__(self, current: int):
        super().__init__(f"ayarlar başka bir kullanıcı tarafından değiştirildi (güncel revizyon {current}); "
                         f"sayfayı yenileyip tekrar deneyin")
        self.current = current


class SettingsInvalid(Exception):
    def __init__(self, message: str, details: list[dict] | None = None):
        super().__init__(message)
        self.details = details or []


# ------------------------------------------------------------------------------------------------ bootstrap
def _packaged_defaults() -> dict:
    return yaml.safe_load(resources.files("airlab_agent").joinpath("defaults.yaml").read_text(encoding="utf-8"))


def _env_set(name: str | None) -> bool:
    return bool(name) and os.getenv(name) not in (None, "")


def bootstrap(rt) -> tuple[dict[str, dict], dict[str, dict[str, str]]]:
    """Ortam + uygulama varsayılanlarından değerler ve alan kaynakları ("env" | "default")."""
    ps, ag, cfg = rt.ps, rt.agent, rt.agent.cfg
    pk = _packaged_defaults()
    b, w, win, sc = cfg["budgets"], cfg["watch"], cfg["windows"], cfg["score"]
    vals: dict[str, dict] = {
        "general": {},
        "agent": {
            "analysis_window_minutes": win["default_minutes"], "baseline_minutes": win["baseline_minutes"],
            "max_window_minutes": win["max_minutes"], "run_timeout_s": ps.investigation_timeout_s,
            "max_concurrent_runs": ps.max_concurrent_investigations, "max_extra_iterations": b["max_iterations"],
            "max_queries_per_run": b["max_queries_per_run"], "query_timeout_s": b["query_timeout_seconds"],
            "query_retries": b["query_retries"], "scheduled_llm": ps.health_check_llm,
            "incident_llm": bool(w.get("llm_on_incident", True)), "watch_confirm_windows": w["confirm_windows"],
            "watch_cooldown_minutes": w["cooldown_minutes"]},
        "scheduling": {
            "health_check_enabled": ps.health_check_minutes > 0,
            "health_check_interval_minutes": max(ps.health_check_minutes, 5) if ps.health_check_minutes > 0 else 15,
            "health_check_window_minutes": ps.health_check_window_minutes, "watch_enabled": ps.watch_enabled,
            "watch_poll_seconds": w["poll_seconds"], "langsmith_sync_enabled": ps.langsmith_sync_minutes > 0,
            "langsmith_sync_minutes": max(ps.langsmith_sync_minutes, 1)},
        "risk": {"weights": dict(sc["weights"]),
                 "ramps": {k: list(sc[k]) for k in ("latency_ratio", "error_rate", "pool_ratio", "lock_waiters", "cpu_pct")},
                 "bands": dict(sc["bands"]), "min_score_coverage": cfg["quality"]["min_score_coverage"]},
        "llm": {},
        "observability": {"langsmith_enabled": bool(ag.langsmith_tracing), "langsmith_project": ag.langsmith_project,
                          "environment_tag": ps.environment},
        "notifications": {"slack_enabled": bool(ps.slack_webhook_url),
                          "slack_webhook_ref": "env:SRE_SLACK_WEBHOOK_URL" if ps.slack_webhook_url else None,
                          "slack_risk_threshold": ps.slack_risk_threshold},
        "retention": {"investigations_days": ps.retention_days, "watch_events_days": ps.retention_days,
                      "llm_runs_days": ps.retention_days},
    }
    # kaynak: ortam değişkeni tanımlıysa ya da AGENT_CONFIG paket varsayılanını değiştirdiyse "env"
    yaml_paths = {
        ("agent", "analysis_window_minutes"): ("windows", "default_minutes"),
        ("agent", "baseline_minutes"): ("windows", "baseline_minutes"),
        ("agent", "max_window_minutes"): ("windows", "max_minutes"),
        ("agent", "max_extra_iterations"): ("budgets", "max_iterations"),
        ("agent", "max_queries_per_run"): ("budgets", "max_queries_per_run"),
        ("agent", "query_timeout_s"): ("budgets", "query_timeout_seconds"),
        ("agent", "query_retries"): ("budgets", "query_retries"),
        ("agent", "incident_llm"): ("watch", "llm_on_incident"),
        ("agent", "watch_confirm_windows"): ("watch", "confirm_windows"),
        ("agent", "watch_cooldown_minutes"): ("watch", "cooldown_minutes"),
        ("scheduling", "watch_poll_seconds"): ("watch", "poll_seconds"),
    }
    sources: dict[str, dict[str, str]] = {}
    for sec, model in SECTIONS.items():
        meta = field_meta(sec)
        sources[sec] = {}
        for name in model.model_fields:
            src = "default"
            if name in vals[sec] and _env_set(meta[name]["env"]):
                src = "env"
            elif (sec, name) in yaml_paths:
                a, k = yaml_paths[(sec, name)]
                if pk.get(a, {}).get(k) != cfg.get(a, {}).get(k):
                    src = "env"
            elif sec == "risk" and name in vals[sec]:
                pkv = {"weights": pk["score"]["weights"], "bands": pk["score"]["bands"],
                       "min_score_coverage": pk["quality"]["min_score_coverage"],
                       "ramps": {k: pk["score"][k] for k in vals["risk"]["ramps"]}}[name]
                if pkv != vals[sec][name]:
                    src = "env"
            sources[sec][name] = src
    return vals, sources


def locks() -> dict[str, dict[str, Any]]:
    """SRE_SETTINGS_LOCKS: 'bölüm.alan=JSON;…' — deployment'ın zorladığı, arayüzden değiştirilemeyen değerler."""
    out: dict[str, dict[str, Any]] = {}
    for item in (x.strip() for x in os.getenv("SRE_SETTINGS_LOCKS", "").split(";")):
        if not item or "=" not in item or "." not in item.split("=", 1)[0]:
            continue
        path, raw = item.split("=", 1)
        sec, name = path.strip().split(".", 1)
        if sec not in SECTIONS or name not in SECTIONS[sec].model_fields:
            log.warning("geçersiz SRE_SETTINGS_LOCKS girdisi yok sayıldı: %s", path)
            continue
        try:
            val = json.loads(raw)
        except json.JSONDecodeError:
            val = raw.strip()
        out.setdefault(sec, {})[name] = val
    return out


# ------------------------------------------------------------------------------------------------ okuma
async def load_rows(s: AsyncSession) -> dict[str, SettingsSection]:
    return {r.section: r for r in (await s.execute(select(SettingsSection))).scalars()}


def _merge(boot: dict, over: dict, lock: dict) -> dict:
    return {**boot, **over, **lock}


def _validate(section: str, data: dict):
    try:
        return SECTIONS[section].model_validate(data)
    except ValidationError as e:
        details = [{"loc": [section, *[str(x) for x in err.get("loc", ())]], "msg": err.get("msg", ""),
                    "type": err.get("type", "")} for err in e.errors()]
        msg = "; ".join(f"{'.'.join(d['loc'][1:]) or section}: {d['msg'].removeprefix('Value error, ')}" for d in details)
        raise SettingsInvalid(msg, details) from None


def effective_from(rt, rows: dict[str, SettingsSection]) -> dict[str, dict]:
    """Tüm bölümlerin etkili değerleri. Kaydı bozuk bir bölüm (ör. elle düzenlenmiş) bootstrap'a döner."""
    boot, _ = bootstrap(rt)
    lk = locks()
    out = {}
    for sec in SECTIONS:
        over = dict(rows[sec].data) if sec in rows else {}
        try:
            out[sec] = _validate(sec, _merge(boot[sec], over, lk.get(sec, {}))).model_dump()
        except SettingsInvalid as e:
            log.error("ayar bölümü geçersiz, ortam/varsayılan değerler kullanılıyor: %s (%s)", sec, e)
            try:
                out[sec] = _validate(sec, _merge(boot[sec], {}, lk.get(sec, {}))).model_dump()
            except SettingsInvalid:
                out[sec] = SECTIONS[sec].model_construct(**boot[sec]).model_dump()
    return out


async def effective(rt, s: AsyncSession) -> dict[str, dict]:
    return effective_from(rt, await load_rows(s))


def view(rt, section: str, rows: dict[str, SettingsSection]) -> dict:
    boot, sources = bootstrap(rt)
    lk = locks().get(section, {})
    row = rows.get(section)
    over = dict(row.data) if row else {}
    eff = effective_from(rt, rows)[section]
    defaults = SECTIONS[section].model_construct().model_dump()
    started = (getattr(rt, "settings_started", None) or {}).get(section, {})
    fields = {}
    for name, meta in field_meta(section).items():
        src = "locked" if name in lk else "admin" if name in over else sources[section][name]
        base = boot[section].get(name, defaults.get(name))
        pending = meta["apply"] == "restart" and name in started and started[name] != eff[name]
        fields[name] = {**meta, "value": eff[name], "source": src, "locked": name in lk,
                        "inherited": base, "restart_pending": pending}
    return {"section": section, "revision": row.revision if row else 0,
            "updated_by": row.updated_by if row else None,
            "updated_at": row.updated_at.isoformat() if row and row.updated_at else None,
            "values": eff, "fields": fields}


# ------------------------------------------------------------------------------------------------ yazma
async def audit(s: AsyncSession, *, actor: str, action: str, section: str, target: str | None = None,
                revision: int | None = None, before: dict | None = None, after: dict | None = None,
                result: str = "ok", detail: str | None = None) -> None:
    s.add(AuditEvent(actor=actor, action=action, section=section, target=target, revision=revision,
                     before=redact(before) if before is not None else None,
                     after=redact(after) if after is not None else None, result=result,
                     detail=(detail or "")[:2000] or None))
    await s.flush()


def _diff(a: dict, b: dict) -> tuple[dict, dict]:
    keys = [k for k in sorted(set(a) | set(b)) if a.get(k) != b.get(k)]
    return {k: a.get(k) for k in keys}, {k: b.get(k) for k in keys}


async def update(rt, s: AsyncSession, section: str, *, revision: int, values: dict | None = None,
                 reset: list[str] | None = None, actor: str, note: str | None = None, check=None,
                 action: str = "update") -> dict:
    """Atomik güncelleme. `check(model)` ek (async) doğrulama: referans bütünlüğü, secret varlığı…"""
    if section not in SECTIONS:
        raise KeyError(section)
    values, reset = dict(values or {}), list(reset or [])
    unknown = [k for k in [*values, *reset] if k not in SECTIONS[section].model_fields]
    if unknown:
        raise SettingsInvalid(f"bilinmeyen alan: {', '.join(unknown)}")
    lk = locks().get(section, {})
    blocked = [k for k in [*values, *reset] if k in lk]
    if blocked:
        await audit(s, actor=actor, action=action, section=section, result="rejected",
                    detail=f"deployment kilidi: {', '.join(blocked)}")
        raise SettingsInvalid(f"deployment lock'u olan alan değiştirilemez: {', '.join(blocked)}")
    row = (await s.execute(select(SettingsSection).where(SettingsSection.section == section)
                           .with_for_update())).scalar_one_or_none()
    current = row.revision if row else 0
    if revision != current:
        raise SettingsConflict(current)
    rows = await load_rows(s)
    before = effective_from(rt, rows)[section]
    boot, _ = bootstrap(rt)
    over = {k: v for k, v in (dict(row.data) if row else {}).items() if k not in reset}
    over.update(values)
    model = _validate(section, _merge(boot[section], over, lk))
    if check is not None:
        await check(model)
    dumped = model.model_dump()
    # yalnız bootstrap'tan farklı, kilitsiz alanları sakla (gerisi ortamı/varsayılanı izler)
    stored = {}
    for k in over:
        if k in lk or k not in dumped:
            continue
        base = boot[section][k] if k in boot[section] else \
            SECTIONS[section].model_fields[k].get_default(call_default_factory=True)
        if dumped[k] != base:
            stored[k] = dumped[k]
    new_rev = current + 1
    if row is None:
        row = SettingsSection(section=section, revision=new_rev, data=stored, updated_by=actor)
        s.add(row)
    else:
        row.revision, row.data, row.updated_by = new_rev, stored, actor
        row.updated_at = datetime.now(timezone.utc)
    s.add(SettingsRevision(section=section, revision=new_rev, data=stored, actor=actor, note=(note or None)))
    await s.flush()
    b, a = _diff(before, dumped)
    await audit(s, actor=actor, action=action, section=section, revision=new_rev, before=b, after=a,
                detail=note)
    rows[section] = row
    return view(rt, section, rows)


async def revisions(s: AsyncSession, section: str, limit: int = 50) -> list[dict]:
    q = (select(SettingsRevision).where(SettingsRevision.section == section)
         .order_by(SettingsRevision.revision.desc()).limit(limit))
    return [{"revision": r.revision, "data": r.data, "actor": r.actor, "note": r.note,
             "created_at": r.created_at.isoformat()} for r in (await s.execute(q)).scalars()]


async def rollback(rt, s: AsyncSession, section: str, *, to_revision: int, revision: int, actor: str,
                   check=None) -> dict:
    """Eski revizyonun verisini yeni revizyon olarak uygular (geçmiş değişmez). Secret'lar geri getirilmez."""
    old = (await s.execute(select(SettingsRevision).where(SettingsRevision.section == section,
                                                          SettingsRevision.revision == to_revision))).scalar_one_or_none()
    if old is None:
        raise SettingsInvalid(f"{section} için {to_revision} numaralı revizyon yok")
    row = await s.get(SettingsSection, section)
    reset = [k for k in (row.data if row else {}) if k not in old.data and k not in locks().get(section, {})]
    vals = {k: v for k, v in copy.deepcopy(old.data).items() if k not in locks().get(section, {})}
    return await update(rt, s, section, revision=revision, values=vals,
                        reset=reset, actor=actor, note=f"rollback → revizyon {to_revision}", check=check,
                        action="rollback")
