"""Arayüzden seçilen Claude modeli (Opus / Sonnet).

Seçim `kv` tablosunda (`llm_model`) tutulur; backend ve worker ayrı süreçler olduğu için her sohbet turu ve her araştırma
başında okunur ve `Llm.set_model` ile uygulanır — yeniden başlatma gerekmez. Kayıt yoksa ortamdaki CLAUDE_MODEL /
CLAUDE_RCA_MODEL geçerlidir. Yalnız izin verilen model kimlikleri kabul edilir.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy.dialects.postgresql import insert

from ..db.models import KeyValue
from ..db.session import transaction

log = logging.getLogger("sre.model")
KEY = "llm_model"
MODELS = {"claude-opus-5-5": "Claude Opus 5.5", "claude-sonnet-5-5": "Claude Sonnet 5.5"}


def _core(rt):
    port = getattr(rt, "llm", None)
    return getattr(port, "llm", None) if port is not None else None


async def refresh(rt) -> str | None:
    """KV'deki seçimi okuyup uygular. Seçili model kimliğini (yoksa None) döndürür; hata akışı durdurmaz."""
    core = _core(rt)
    if core is None or not hasattr(core, "set_model"):
        return None
    try:
        async with transaction(rt.sm) as s:
            kv = await s.get(KeyValue, KEY)
            model = (kv.value or {}).get("model") if kv else None
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
    async with transaction(rt.sm) as s:
        stmt = insert(KeyValue).values(key=KEY, value={"model": model, "by": username,
                                                       "at": datetime.now(timezone.utc).isoformat()})
        await s.execute(stmt.on_conflict_do_update(index_elements=[KeyValue.key], set_={"value": stmt.excluded.value}))
    core = _core(rt)
    if core is not None and hasattr(core, "set_model"):
        core.set_model(model)
    log.info("LLM modeli değiştirildi: %s (%s)", model, username)
    return await state(rt)


async def state(rt) -> dict:
    await refresh(rt)
    core = _core(rt)
    async with transaction(rt.sm) as s:
        kv = await s.get(KeyValue, KEY)
    meta = (kv.value if kv else {}) or {}
    return {"enabled": core is not None, "model": core.model_name if core else None,
            "rca_model": getattr(core, "rca_model_name", None) if core else None,
            "options": [{"id": k, "label": v} for k, v in MODELS.items()],
            "changed_by": meta.get("by"), "changed_at": meta.get("at")}
