"""Zamanlanmış işler. Bir işi almak (claim) tek SQL ile yapılır:
`FOR UPDATE SKIP LOCKED` + kira süresi (locked_until). İki worker aynı işi aynı anda alamaz; worker çökerse kira
dolunca iş başka worker'a geçer. `next_run_at` iş BAŞLARKEN ilerletilir (aynı periyot iki kez çalışmaz)."""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select, text, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from airlab_agent.redact import redact, redact_text

from ..db.models import JobRun, JobStatus, ScheduledJob

CLAIM_SQL = text("""
WITH due AS (
  SELECT id FROM sre.scheduled_jobs
   WHERE enabled AND next_run_at <= now() AND (locked_until IS NULL OR locked_until < now())
   ORDER BY next_run_at
   LIMIT :n
   FOR UPDATE SKIP LOCKED)
UPDATE sre.scheduled_jobs j
   SET locked_by = :worker, locked_until = now() + make_interval(secs => :lease),
       next_run_at = greatest(j.next_run_at + make_interval(secs => j.interval_seconds),
                              now() + make_interval(secs => j.interval_seconds / 2)),
       last_run_at = now(), updated_at = now()
  FROM due WHERE j.id = due.id
RETURNING j.id
""")


async def ensure(s: AsyncSession, name: str, job_type: str, interval_seconds: int, params: dict | None = None,
                 enabled: bool = True) -> None:
    """Tanımı oluşturur; varsa aralık/parametre/etkinlik yapılandırmadan güncellenir (çalışma durumu korunur)."""
    stmt = insert(ScheduledJob).values(name=name, job_type=job_type, interval_seconds=interval_seconds,
                                       params=params or {}, enabled=enabled)
    await s.execute(stmt.on_conflict_do_update(index_elements=[ScheduledJob.name],
                                               set_={"job_type": stmt.excluded.job_type,
                                                     "interval_seconds": stmt.excluded.interval_seconds,
                                                     "params": stmt.excluded.params,
                                                     "enabled": stmt.excluded.enabled}))


async def claim(s: AsyncSession, worker: str, lease_seconds: int, n: int = 1) -> list[ScheduledJob]:
    ids = [r[0] for r in (await s.execute(CLAIM_SQL, {"worker": worker, "lease": lease_seconds, "n": n})).all()]
    if not ids:
        return []
    return list((await s.execute(select(ScheduledJob).where(ScheduledJob.id.in_(ids))
                                 .execution_options(populate_existing=True))).scalars())


async def renew(s: AsyncSession, job_id: uuid.UUID, worker: str, lease_seconds: int) -> bool:
    """Kirayı işin başlama anından itibaren yeniler. Kira başka worker'a geçtiyse False döner: iş çalıştırılmaz.

    Bir turda birden çok iş kiralanıp sırayla çalıştırılır; öndeki iş uzun sürerse sıradakinin kirası başlamadan
    dolabilir ve başka worker aynı işi alabilir. Bu kontrol aynı işin iki kez çalışmasını önler."""
    res = await s.execute(text("""UPDATE sre.scheduled_jobs
                                     SET locked_until = now() + make_interval(secs => :lease)
                                   WHERE id = :id AND locked_by = :worker"""),
                          {"lease": lease_seconds, "id": job_id, "worker": worker})
    return bool(res.rowcount)


async def start_run(s: AsyncSession, job_id: uuid.UUID, worker: str) -> JobRun:
    r = JobRun(job_id=job_id, worker_id=worker, status=JobStatus.RUNNING)
    s.add(r)
    await s.flush()
    return r


async def finish_run(s: AsyncSession, run_id: uuid.UUID, job_id: uuid.UUID, worker: str, *, ok: bool,
                     result: dict[str, Any] | None = None, error: str | None = None) -> None:
    await s.execute(update(JobRun).where(JobRun.id == run_id).values(
        status=JobStatus.SUCCEEDED if ok else JobStatus.FAILED, finished_at=datetime.now(timezone.utc),
        result=redact(result or {}), error=redact_text(error)[:4000] if error else None))
    # kirayı yalnız hâlâ bizdeyse bırak
    await s.execute(update(ScheduledJob).where(ScheduledJob.id == job_id, ScheduledJob.locked_by == worker)
                    .values(locked_by=None, locked_until=None))


async def list_jobs(s: AsyncSession) -> list[ScheduledJob]:
    return list((await s.execute(select(ScheduledJob).order_by(ScheduledJob.name))).scalars())


async def recent_runs(s: AsyncSession, job_id: uuid.UUID | None = None, limit: int = 20) -> list[JobRun]:
    stmt = select(JobRun).order_by(JobRun.started_at.desc()).limit(limit)
    if job_id:
        stmt = stmt.where(JobRun.job_id == job_id)
    return list((await s.execute(stmt)).scalars())


async def set_enabled(s: AsyncSession, name: str, enabled: bool) -> None:
    await s.execute(update(ScheduledJob).where(ScheduledJob.name == name).values(enabled=enabled))


async def prune_runs(s: AsyncSession, keep_days: int) -> int:
    cut = datetime.now(timezone.utc) - timedelta(days=keep_days)
    res = await s.execute(text("DELETE FROM sre.job_runs WHERE started_at < :cut"), {"cut": cut})
    return res.rowcount or 0
