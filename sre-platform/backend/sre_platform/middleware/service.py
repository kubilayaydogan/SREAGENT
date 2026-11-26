"""Middleware yapılandırma yönetimi: kodda kayıtlı tanım + versiyonlu yapılandırma.

* Tanım kodda (airlab_agent.middleware.REGISTRY); arayüzden kod çalıştırılamaz, yalnız kayıtlı middleware'ler
  yapılandırılır. Parametreler backend'de middleware'in Pydantic şemasıyla doğrulanır.
* Optimistic concurrency: istek `version` taşır; mevcut sürüm farklıysa 409 (kayıp güncelleme olmaz).
* Sıralama çakışması (aynı order) ve geçersiz parametre 422. Zorunlu güvenlik middleware'i devre dışı bırakılamaz ve
  kapsamı daraltılamaz (backend kontrolü; rol fark etmez).
* Her değişiklik: değişmez sürüm kaydı + audit (kim / ne zaman / ne). Red de audit'lenir (ayrı transaction).
* Run başında `snapshot` alınır; değişiklik yeni run'lara uygulanır.
"""
from __future__ import annotations

import copy
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from airlab_agent import middleware as mw
from airlab_agent.redact import redact

from ..db.models import MiddlewareConfig, MiddlewareConfigVersion

SECTION = "middleware"


class MiddlewareError(Exception):
    def __init__(self, status: int, message: str, detail=None):
        super().__init__(message)
        self.status, self.message, self.detail = status, message, detail


def _row_cfg(r: MiddlewareConfig) -> dict:
    return {"enabled": r.enabled, "order": r.order, "params": dict(r.params or {}), "scope": dict(r.scope or {}),
            "version": r.version, "updated_at": r.updated_at.isoformat() if r.updated_at else None,
            "updated_by": r.updated_by}


async def load(s: AsyncSession) -> dict[str, dict]:
    """Kayıtlı her middleware için etkili yapılandırma (satır yoksa kod varsayılanı, version 0)."""
    rows = {r.name: r for r in (await s.execute(select(MiddlewareConfig))).scalars()}
    out = mw.default_config()
    for n in out:
        if n in rows:
            c = _row_cfg(rows[n])
            m = mw.REGISTRY[n]
            try:                                  # kod parametre şeması değiştiyse eksik alanlar varsayılanla dolar
                c["params"] = m.Params(**c["params"]).model_dump()
            except Exception:  # noqa: BLE001 — geçersiz kalmış parametre: güvenli varsayılan + işaret
                c["params"], c["invalid_stored_params"] = m.Params().model_dump(), True
            if m.mandatory:
                c["enabled"], c["scope"] = True, {}
            out[n] = c
        else:
            out[n].update(updated_at=None, updated_by=None)
    return out


async def snapshot(s: AsyncSession) -> dict[str, dict]:
    """Run başı değişmez kopya (yalnız çalışma için gerekli alanlar)."""
    return {n: {k: copy.deepcopy(c[k]) for k in ("enabled", "order", "params", "scope", "version")}
            for n, c in (await load(s)).items()}


def masked(cfg: dict) -> dict:
    return redact(copy.deepcopy(cfg), "audit")


async def _audit(s: AsyncSession, *, actor: str, action: str, target: str, before=None, after=None,
                 result: str = "ok", detail: str | None = None, revision: int | None = None) -> None:
    from ..security import audit as sec_audit
    if result != "ok":
        await sec_audit.record_separate(async_sessionmaker(s.bind, expire_on_commit=False), actor=actor,
                                        action=action, section=SECTION, target=target, before=before, after=after,
                                        result=result, detail=detail, revision=revision)
        return
    await sec_audit.record(s, actor=actor, action=action, section=SECTION, target=target, before=before,
                           after=after, result=result, detail=detail, revision=revision)


async def _reject(s, *, actor: str, name: str, status: int, message: str, action: str = "middleware_update"):
    await _audit(s, actor=actor, action=action, target=name, result="rejected", detail=message)
    raise MiddlewareError(status, message)


async def _apply(s: AsyncSession, current: dict, name: str, values: dict, *, actor: str, note: str | None,
                 now: datetime) -> tuple[dict, dict]:
    """Tek middleware'e değişiklik (doğrulanmış). Döner: (önce, sonra)."""
    before = current[name]
    merged = {"enabled": values.get("enabled", before["enabled"]), "order": values.get("order", before["order"]),
              "params": values.get("params", before["params"]), "scope": values.get("scope", before["scope"])}
    clean = mw.validate_config(name, merged)
    row = await s.get(MiddlewareConfig, name, with_for_update=True)
    new_version = (row.version if row else 0) + 1
    if row is None:
        row = MiddlewareConfig(name=name, enabled=clean["enabled"], order=clean["order"], params=clean["params"],
                               scope=clean["scope"], version=new_version, updated_at=now, updated_by=actor)
        s.add(row)
    else:
        row.enabled, row.order, row.params, row.scope = clean["enabled"], clean["order"], clean["params"], clean["scope"]
        row.version, row.updated_at, row.updated_by = new_version, now, actor
    change = ("enable" if clean["enabled"] else "disable") if clean["enabled"] != before["enabled"] else \
        "reorder" if set(values) == {"order"} else "update"
    s.add(MiddlewareConfigVersion(name=name, version=new_version, change=change,
                                  snapshot=masked({**clean, "version": new_version}), actor=actor,
                                  note=(note or None) and note[:1000], created_at=now))
    after = {**clean, "version": new_version}
    return ({k: before[k] for k in ("enabled", "order", "params", "scope", "version")}, after)


async def update(s: AsyncSession, name: str, *, version: int, values: dict, actor: str,
                 note: str | None = None) -> dict:
    if name not in mw.REGISTRY:
        raise MiddlewareError(404, "kayıtlı olmayan middleware")
    allowed = {"enabled", "order", "params", "scope"}
    unknown = set(values) - allowed
    if unknown:
        await _reject(s, actor=actor, name=name, status=422, message=f"bilinmeyen alan: {sorted(unknown)}")
    current = await load(s)
    if int(version) != int(current[name]["version"]):
        await _reject(s, actor=actor, name=name, status=409,
                      message=f"yapılandırma başka biri tarafından değiştirildi (beklenen v{version}, "
                              f"güncel v{current[name]['version']}); yenileyip tekrar deneyin")
    try:
        proposed = {n: dict(c) for n, c in current.items()}
        proposed[name] = {**proposed[name], **values}
        mw.check_orders(proposed)
        now = datetime.now(timezone.utc)
        before, after = await _apply(s, current, name, values, actor=actor, note=note, now=now)
        await s.flush()
    except ValueError as e:
        await _reject(s, actor=actor, name=name, status=422, message=str(e)[:500])
    except IntegrityError:
        await _reject(s, actor=actor, name=name, status=409, message="eşzamanlı değişiklik; yenileyip tekrar deneyin")
    await _audit(s, actor=actor, action="middleware_update", target=name, before=masked(before), after=masked(after),
                 revision=after["version"], detail=note)
    return (await load(s))[name]


async def reorder(s: AsyncSession, items: list[dict], *, actor: str, note: str | None = None) -> dict[str, dict]:
    """Birden çok middleware'in order'ı tek transaction'da; her öğe kendi `version`'ını taşır."""
    current = await load(s)
    proposed = {n: dict(c) for n, c in current.items()}
    for it in items:
        n = it.get("name")
        if n not in mw.REGISTRY:
            await _reject(s, actor=actor, name=str(n)[:64], status=404, message="kayıtlı olmayan middleware",
                          action="middleware_reorder")
        if int(it.get("version", -1)) != int(current[n]["version"]):
            await _reject(s, actor=actor, name=n, status=409, action="middleware_reorder",
                          message=f"{n}: yapılandırma başka biri tarafından değiştirildi; yenileyip tekrar deneyin")
        proposed[n]["order"] = it.get("order")
    try:
        for n, c in proposed.items():
            if not isinstance(c["order"], int) or not 1 <= c["order"] <= 1000:
                raise ValueError(f"{n}: order 1..1000 arasında tam sayı olmalı")
        mw.check_orders(proposed)
        now = datetime.now(timezone.utc)
        changes = []
        for it in items:
            n = it["name"]
            if proposed[n]["order"] != current[n]["order"]:
                changes.append((n, *await _apply(s, current, n, {"order": proposed[n]["order"]}, actor=actor,
                                                 note=note, now=now)))
        await s.flush()
    except ValueError as e:
        await _reject(s, actor=actor, name="*", status=422, message=str(e)[:500], action="middleware_reorder")
    except IntegrityError:
        await _reject(s, actor=actor, name="*", status=409, message="eşzamanlı değişiklik; yenileyip tekrar deneyin",
                      action="middleware_reorder")
    for n, b, a in changes:
        await _audit(s, actor=actor, action="middleware_reorder", target=n, before={"order": b["order"]},
                     after={"order": a["order"]}, revision=a["version"], detail=note)
    return await load(s)


async def versions(s: AsyncSession, name: str, limit: int = 50) -> list[dict]:
    rows = (await s.execute(select(MiddlewareConfigVersion).where(MiddlewareConfigVersion.name == name)
                            .order_by(MiddlewareConfigVersion.version.desc()).limit(limit))).scalars()
    return [{"version": r.version, "change": r.change, "snapshot": r.snapshot, "actor": r.actor, "note": r.note,
             "created_at": r.created_at.isoformat()} for r in rows]
