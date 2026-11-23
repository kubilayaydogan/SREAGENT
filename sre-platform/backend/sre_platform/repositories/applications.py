from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models import Application
from .base import NotFound

DESCRIPTIONS = {
    "reservation-service": ("Reservation", "Rezervasyon oluşturma/görüntüleme (hot-row lock senaryolarının source'u olabilir)"),
    "checkin-service": ("Check-in", "Yolcu check-in işlemleri"),
    "flightops-service": ("Flight Ops", "Uçuş operasyonları"),
    "baggage-service": ("Baggage", "Bagaj takibi"),
    "chaos-controller": ("Chaos Controller", "Kontrollü yük/kaos üreticisi (araç)"),
    "pg-lock-collector": ("PG Lock Collector", "PostgreSQL lock/session collector (araç)"),
    "sre-agent": ("SRE Agent", "Bu platformun analiz çekirdeği (araç)"),
}


async def sync_from_config(s: AsyncSession, cfg: dict) -> int:
    """Agent yapılandırmasındaki uygulamaları kaydeder (idempotent). Kaynak gerçek: airlab_agent defaults.yaml."""
    role_by_app = {v: k for k, v in cfg.get("db_role_to_app", {}).items()}
    rows = []
    for kind, apps in (("business", cfg.get("apps", [])), ("tooling", cfg.get("tooling_apps", []))):
        for a in apps:
            name, desc = DESCRIPTIONS.get(a, (a, None))
            rows.append({"id": a, "name": name, "description": desc, "kind": kind,
                         "host": cfg.get("hosts", {}).get("app"), "db_role": role_by_app.get(a)})
    if not rows:
        return 0
    stmt = insert(Application).values(rows)
    stmt = stmt.on_conflict_do_update(index_elements=[Application.id],
                                      set_={c: stmt.excluded[c] for c in ("name", "description", "kind", "host", "db_role")})
    await s.execute(stmt)
    return len(rows)


async def list_(s: AsyncSession, kind: str | None = None) -> list[Application]:
    stmt = select(Application).order_by(Application.kind, Application.id)
    if kind:
        stmt = stmt.where(Application.kind == kind)
    return list((await s.execute(stmt)).scalars())


async def get(s: AsyncSession, app_id: str) -> Application:
    a = await s.get(Application, app_id)
    if a is None:
        raise NotFound(f"uygulama bulunamadı: {app_id}")
    return a
