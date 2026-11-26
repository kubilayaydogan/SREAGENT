"""Guardrail kayıtlarının saklama süresi (Guardrails › Settings › retention).

* değerlendirmeler `evaluation_days`, güvenli snapshot'lar `snapshot_days` (satır kalır, snapshot boşaltılır),
  test koşuları `test_run_days`, kapanmış ihlaller `evaluation_days` sonra silinir.
* audit olayları `audit_days` sonra yalnız en eskiden başlayarak silinir (append-only tetikleyicisi yalnız bu oturum
  ayarıyla DELETE'e izin verir); kalan zincir en eski kaydın prev_hash'inden doğrulanır.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from airlab_agent.guardrails.engine import merged_config
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


async def prune(s: AsyncSession, now: datetime | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    data = (await s.execute(text("SELECT data FROM sre.guardrail_config WHERE id = 1"))).scalar_one_or_none()
    r = merged_config(data or {}).get("retention") or {}

    def cut(k: str, d: int) -> datetime:
        return now - timedelta(days=max(1, int(r.get(k) or d)))
    out = {}
    out["guardrail_evaluations"] = (await s.execute(text(
        "DELETE FROM sre.guardrail_evaluations WHERE ts < :c"), {"c": cut("evaluation_days", 30)})).rowcount or 0
    out["guardrail_snapshots"] = (await s.execute(text(
        "UPDATE sre.guardrail_evaluations SET snapshot = NULL WHERE snapshot IS NOT NULL AND ts < :c"),
        {"c": cut("snapshot_days", 7)})).rowcount or 0
    out["guardrail_test_runs"] = (await s.execute(text(
        "DELETE FROM sre.guardrail_test_runs WHERE created_at < :c"), {"c": cut("test_run_days", 30)})).rowcount or 0
    out["guardrail_violations_closed"] = (await s.execute(text(
        "DELETE FROM sre.guardrail_violations WHERE status IN ('resolved','false_positive') AND last_seen < :c"),
        {"c": cut("evaluation_days", 30)})).rowcount or 0
    await s.execute(text("SET LOCAL sre.guardrail_audit_prune = 'on'"))
    out["guardrail_audit_events"] = (await s.execute(text(
        "DELETE FROM sre.guardrail_audit_events WHERE ts < :c"), {"c": cut("audit_days", 365)})).rowcount or 0
    await s.execute(text("SET LOCAL sre.guardrail_audit_prune = 'off'"))
    return out
