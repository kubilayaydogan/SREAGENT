"""Arayüzden seçilen Claude modeli (üst bar: Opus / Sonnet).

Seçim Settings'in `llm.model_override` alanında tutulur (revizyonlu, audit'li); ayrı bir yapılandırma sistemi yoktur.
Backend ve worker ayrı süreçler olduğu için her sohbet turu ve her investigation başında okunur ve uygulanır —
yeniden başlatma gerekmez. Seçim yoksa LLM profili / ortamdaki CLAUDE_MODEL ve CLAUDE_RCA_MODEL geçerlidir.
Eski sürümün `kv.llm_model` kaydı açılışta bir kez Settings'e taşınır. API sözleşmesi (GET/PUT /system/llm-model) aynıdır.
"""
from __future__ import annotations

import logging

from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models import KeyValue, SettingsSection
from ..db.session import transaction

log = logging.getLogger("sre.model")
KEY = "llm_model"
MODELS = {"claude-opus-5-5": "Claude Opus 5.5", "claude-sonnet-5-5": "Claude Sonnet 5.5"}


def _core(rt):
    port = getattr(rt, "llm", None)
    return getattr(port, "llm", None) if port is not None else None


async def _section(rt, s: AsyncSession) -> tuple[dict, SettingsSection | None]:
    from ..settings import service
    rows = await service.load_rows(s)
    return service.effective_from(rt, rows)["llm"], rows.get("llm")


async def refresh(rt) -> str | None:
    """Etkili seçimi okuyup uygular. Seçili model kimliğini (yoksa None) döndürür; hata akışı durdurmaz."""
    core = _core(rt)
    if core is None or not hasattr(core, "set_model"):
        return None
    try:
        async with transaction(rt.sm) as s:
            llm, _ = await _section(rt, s)
        model = llm.get("model_override")
    except Exception:  # noqa: BLE001
        log.exception("model seçimi okunamadı; mevcut model korunuyor")
        return core.model_name
    if model not in MODELS:
        model = None
    core.set_model(model)
    return model


async def select(rt, model: str, *, username: str) -> dict:
    if model not in MODELS:
        raise ValueError(f"desteklenmeyen model: {model}")
    from ..settings import service
    async with transaction(rt.sm) as s:
        _, row = await _section(rt, s)
        await service.update(rt, s, "llm", revision=row.revision if row else 0, values={"model_override": model},
                             actor=username, note="üst bar model seçimi", action="model_select")
    core = _core(rt)
    if core is not None and hasattr(core, "set_model"):
        core.set_model(model)
    log.info("LLM modeli değiştirildi: %s (%s)", model, username)
    return await state(rt)


async def state(rt) -> dict:
    await refresh(rt)
    core = _core(rt)
    async with transaction(rt.sm) as s:
        _, row = await _section(rt, s)
    return {"enabled": core is not None, "model": core.model_name if core else None,
            "rca_model": getattr(core, "rca_model_name", None) if core else None,
            "options": [{"id": k, "label": v} for k, v in MODELS.items()],
            "changed_by": row.updated_by if row else None,
            "changed_at": row.updated_at.isoformat() if row and row.updated_at else None}


async def migrate_legacy(rt, s: AsyncSession) -> None:
    """kv.llm_model → Settings llm.model_override (yalnız Settings'te seçim yoksa; bir kez)."""
    from ..settings import service
    kv = await s.get(KeyValue, KEY)
    if kv is None:
        return
    model = (kv.value or {}).get("model")
    row = await s.get(SettingsSection, "llm")
    if model in MODELS and not (row and "model_override" in (row.data or {})):
        await service.update(rt, s, "llm", revision=row.revision if row else 0, values={"model_override": model},
                             actor=(kv.value or {}).get("by") or "migration", note="kv.llm_model taşındı",
                             action="migrate")
    await s.delete(kv)
    await s.flush()
