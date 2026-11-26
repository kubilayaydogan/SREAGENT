"""Veri saklama (retention) — P2.7. Worker `retention` işi (saatlik) ve önizleme ucu aynı planı kullanır.

Kategoriler ve varsayılanlar (Settings › Retention):
* investigations / watch_events / llm_runs / job_runs — mevcut süreler (incident'a bağlı, RCA'lı, feedback'li ya da
  sohbette olan investigation saklanır).
* llm_usage, llm_context_calls — LLM Monitoring süresiyle aynı (`llm_runs_days`).
* auth_login_failures — 1 gün (throttle penceresi 5 dk; daha eskisinin işlevi yok).
* trace_links (sohbet turu eşlemeleri) — LLM Monitoring süresiyle aynı; investigation eşlemeleri investigation'la.
* agent_trace_runs (+ olayları) — Agent Trace kayıtları; LLM Monitoring süresiyle.
* Sahipsiz checkpoint'ler (varsayılan AÇIK) — thread_id'si hiçbir investigation run_id'sine ya da konuşma
  thread_id'sine karşılık gelmeyen ve son checkpoint'i 1 günden eski thread'ler (investigation retention'la
  silinince ya da konuşma kaldırılınca kalan artıklar).
* Checkpoint TTL (varsayılan KAPALI, 0) — son checkpoint'i N günden eski thread'ler; aktif (PENDING/RUNNING)
  investigation'ın thread'i asla silinmez.
* Store — süresi dolmuş (`expires_at`) öğeler her zaman; yaş bazlı silme (`store_days`) varsayılan KAPALI.

Audit kayıtları SİLİNMEZ (append-only tetikleyiciler; audit retention P1.5 DB rol ayrımına bağlı ve ertelendi).
Her silme turu, bir şey sildiyse, kategori sayılarıyla tek `retention.purge` audit kaydı bırakır.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

ORPHAN_GRACE = timedelta(days=1)
LOGIN_FAILURE_KEEP = timedelta(days=1)
MAX_THREADS_PER_RUN = 1000

_INV_KEEP = """i.created_at < :cut
        AND NOT EXISTS (SELECT 1 FROM sre.incident_investigations x WHERE x.investigation_id = i.id)
        AND NOT EXISTS (SELECT 1 FROM sre.rca_reports r WHERE r.investigation_id = i.id)
        AND NOT EXISTS (SELECT 1 FROM sre.agent_feedback f WHERE f.investigation_id = i.id)
        AND i.conversation_id IS NULL"""

# (ad, tablo + koşul, kesim gün alanı | sabit süre)
ROW_RULES: tuple[tuple[str, str, str, Any], ...] = (
    ("investigations", "sre.investigations i", _INV_KEEP, "investigations_days"),
    ("watch_events", "sre.watch_events", "ts < :cut", "watch_events_days"),
    ("job_runs", "sre.job_runs", "started_at < :cut", "job_runs_days"),
    ("llm_runs", "sre.llm_runs", "start_time < :cut", "llm_runs_days"),
    ("llm_usage", "sre.llm_usage", "ts < :cut", "llm_runs_days"),
    ("llm_context_calls", "sre.llm_context_calls", "started_at < :cut", "llm_runs_days"),
    ("agent_runs", "sre.agent_runs", "started_at < :cut AND status <> 'running'", "job_runs_days"),
    ("auth_login_failures", "sre.auth_login_failures", "ts < :cut", LOGIN_FAILURE_KEEP),
    # investigation'a bağlı eşlemeler investigation'la (CASCADE) silinir; sohbet turu eşlemeleri LLM süresiyle
    ("trace_links", "sre.trace_links", "created_at < :cut AND investigation_id IS NULL", "llm_runs_days"),
    # Agent Trace: maskeli prompt/state kopyaları — LLM Monitoring süresiyle; olaylar CASCADE ile silinir
    ("agent_trace_runs", "sre.agent_trace_runs", "created_at < :cut", "llm_runs_days"),
)

_THREADS = """SELECT c.thread_id FROM langgraph.checkpoints c GROUP BY c.thread_id
    HAVING max((c.checkpoint->>'ts')::timestamptz) < :cut"""
_ORPHAN = """ AND NOT EXISTS (SELECT 1 FROM sre.investigations i WHERE i.run_id = t.thread_id)
    AND NOT EXISTS (SELECT 1 FROM sre.conversations v WHERE v.thread_id = t.thread_id)"""
_NOT_ACTIVE = """ AND NOT EXISTS (SELECT 1 FROM sre.investigations i WHERE i.run_id = t.thread_id
    AND i.status::text IN ('PENDING', 'RUNNING'))"""


def _cut(now: datetime, spec: Any, ret: dict) -> datetime:
    return now - (spec if isinstance(spec, timedelta) else timedelta(days=int(ret[spec])))


async def _threads(s: AsyncSession, cut: datetime, cond: str) -> list[str]:
    q = f"SELECT t.thread_id FROM ({_THREADS}) t WHERE true{cond} LIMIT {MAX_THREADS_PER_RUN}"
    return [r[0] for r in await s.execute(text(q), {"cut": cut})]


async def plan(s: AsyncSession, ret: dict, now: datetime | None = None) -> dict[str, Any]:
    """Silinecekleri SAYAR (silmez). Önizleme ve audit için."""
    now = now or datetime.now(timezone.utc)
    out: dict[str, Any] = {}
    for name, table, cond, spec in ROW_RULES:
        out[name] = int(await s.scalar(text(f"SELECT count(*) FROM {table} WHERE {cond}"),
                                       {"cut": _cut(now, spec, ret)}) or 0)
    orphan = await _threads(s, now - ORPHAN_GRACE, _ORPHAN) if ret.get("orphan_checkpoints_cleanup", True) else []
    ttl = (await _threads(s, now - timedelta(days=int(ret["checkpoints_days"])), _NOT_ACTIVE)
           if int(ret.get("checkpoints_days") or 0) > 0 else [])
    out["checkpoint_threads_orphan"] = len(orphan)
    out["checkpoint_threads_ttl"] = len(set(ttl) - set(orphan))
    out["_threads"] = sorted(set(orphan) | set(ttl))
    store_cond = "expires_at IS NOT NULL AND expires_at < :now"
    params: dict[str, Any] = {"now": now}
    if int(ret.get("store_days") or 0) > 0:
        store_cond = f"({store_cond}) OR updated_at < :scut"
        params["scut"] = now - timedelta(days=int(ret["store_days"]))
    out["store_items"] = int(await s.scalar(text(f"SELECT count(*) FROM langgraph.store WHERE {store_cond}"), params)
                             or 0)
    out["_store"] = (store_cond, params)
    return out


async def purge(s: AsyncSession, ret: dict, now: datetime | None = None) -> dict[str, Any]:
    """Planı uygular (çağıranın transaction'ında). Sayıları döner."""
    now = now or datetime.now(timezone.utc)
    p = await plan(s, ret, now)
    out: dict[str, Any] = {}
    for name, table, cond, spec in ROW_RULES:
        res = await s.execute(text(f"DELETE FROM {table} WHERE {cond}"), {"cut": _cut(now, spec, ret)})
        out[name] = res.rowcount or 0
    threads = p.pop("_threads")
    if threads:
        for tbl in ("checkpoint_writes", "checkpoint_blobs", "checkpoints"):
            await s.execute(text(f"DELETE FROM langgraph.{tbl} WHERE thread_id = ANY(:t)"), {"t": threads})
    out["checkpoint_threads_orphan"] = p["checkpoint_threads_orphan"]
    out["checkpoint_threads_ttl"] = p["checkpoint_threads_ttl"]
    store_cond, params = p.pop("_store")
    out["store_items"] = (await s.execute(text(f"DELETE FROM langgraph.store WHERE {store_cond}"), params)).rowcount or 0
    return out


def public(p: dict) -> dict:
    return {k: v for k, v in p.items() if not k.startswith("_")}
