"""sre-worker: zamanlanmış işler. Birden fazla worker çalışsa bile bir iş aynı anda tek worker'da çalışır:
iş satırı `FOR UPDATE SKIP LOCKED` ile kiralanır (lease) ve next_run_at ileri alınır. Worker çökerse kira süresi
dolunca iş başka worker'a geçer.

İşler:
  watch         (poll_seconds)        airlab_agent.watch durum makinesi, PG durumlu → incident + araştırma
  health_check  (SRE_HEALTH_CHECK_MINUTES) son N dakikanın tam analizi (LLM'siz) → araştırma kaydı
  retention     (saatlik)             eski kayıtların temizliği + süresi dolan onaylar
  langsmith_sync (SRE_LANGSMITH_SYNC_MINUTES) LangSmith trace/LLM özetleri → sre.llm_runs (LLM izleme panosu)
"""
from __future__ import annotations

import asyncio
import logging
import signal
import time
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import text

from .core.config import PlatformSettings
from .core.logging import new_correlation_id, correlation_id
from .db.models import IncidentSource, InvestigationStatus, Severity, Trigger
from .db.session import transaction
from .repositories import approvals, incidents, investigations, jobs
from .repositories.base import Conflict

log = logging.getLogger("sre.worker")
STOP = asyncio.Event()


async def _noop(*_a, **_k):
    return None


# ---------------------------------------------------------------- işler
class WatchJob:
    """Watcher thread'de çalışır; PG durumu kalıcıdır, worker yeniden başlasa da aynı pencere iki kez sayılmaz."""

    def __init__(self, rt):
        from .services.watch_pg import PgStateStore, PlatformWatcher
        self.store = PgStateStore(rt.ps.libpq_url)
        self.watcher = PlatformWatcher(rt, asyncio.get_running_loop(), self.store)

    async def __call__(self, rt, params: dict) -> dict[str, Any]:
        r = await asyncio.to_thread(self.watcher.tick)
        return {**r, **self.watcher.status()}

    def close(self):
        self.store.close()


async def health_check(rt, params: dict) -> dict[str, Any]:
    from airlab_agent.graph import new_run_id

    from .agent.investigation import InvestigationRunner
    ps = rt.ps
    from .settings import runtime as srt
    eff = await srt.effective(rt)
    try:
        srt.check_allowed(eff, "scheduled")
    except srt.RunNotAllowed as e:
        return {"skipped": str(e)}
    lag = rt.agent.cfg["windows"]["ingest_lag_seconds"]
    end = (datetime.now(timezone.utc) - timedelta(seconds=lag)).replace(second=0, microsecond=0)
    start = end - timedelta(minutes=int(params.get("window_minutes", ps.health_check_window_minutes)))
    async with transaction(rt.sm) as s:
        inv = await investigations.create(s, run_id=new_run_id("hc"), trigger=Trigger.SCHEDULED,
                                          title=f"Zamanlanmış sağlık kontrolü {start:%H:%M}–{end:%H:%M} UTC",
                                          window_start=start, window_end=end,
                                          description="Periyodik analiz (sre-worker)")
        inv_id = inv.id
    llm = rt.llm.llm if (eff["agent"]["scheduled_llm"] and rt.llm is not None and hasattr(rt.llm, "llm")) else None
    async with rt.sem:
        done, report = await InvestigationRunner(rt).run(inv_id, emit=_noop, mode="scheduled", core_llm=llm,
                                                         notes=["zamanlanmış sağlık kontrolü"])
    if report:
        await rt.memory.remember_investigation(done, user_id=None, conversation_id=None)
    out: dict[str, Any] = {"investigation_id": str(done.id), "status": done.status.value,
                           "severity": done.severity.value, "mechanism": done.primary_mechanism}
    if done.status == InvestigationStatus.FAILED:          # iş geçmişinde başarısız görünsün (araştırma kaydı da FAILED)
        raise RuntimeError(f"sağlık kontrolü analizi başarısız (araştırma {done.id}): {done.error or '?'}"[:1500])
    # Yüksek/kritik ve mekanizması belli bulgu → incident (aynı mekanizma için tek açık kayıt). Watch açık bir
    # incident yürütüyorsa yalnız ona bağlanır (çift incident yok).
    if done.status == InvestigationStatus.COMPLETED and done.severity in (Severity.HIGH, Severity.CRITICAL) \
            and done.primary_mechanism:
        async with transaction(rt.sm) as s:
            target = await incidents.open_by_dedup(s, "watch") or \
                await incidents.open_by_dedup(s, f"health:{done.primary_mechanism}")
            if target is None:
                try:
                    target = await incidents.create(
                        s, title=f"Sağlık kontrolü: {done.root_cause_hypothesis or done.primary_mechanism}"[:300],
                        source=IncidentSource.AGENT, severity=done.severity, investigation_id=done.id,
                        application_id=done.application_id, dedup_key=f"health:{done.primary_mechanism}",
                        description="Zamanlanmış health check yüksek riskli bir mechanism tespit etti.")
                    out["incident_created"] = True
                except Conflict:
                    target = await incidents.open_by_dedup(s, f"health:{done.primary_mechanism}")
            if target is not None:
                await incidents.link_investigation(s, target.id, done.id, relation="related")
                out["incident_id"] = str(target.id)
    return out


RETENTION_SQL = [
    # incident'a bağlı veya RCA'sı olan araştırmalar saklanır
    ("investigations", """DELETE FROM sre.investigations i WHERE i.created_at < :cut
        AND NOT EXISTS (SELECT 1 FROM sre.incident_investigations x WHERE x.investigation_id = i.id)
        AND NOT EXISTS (SELECT 1 FROM sre.rca_reports r WHERE r.investigation_id = i.id)
        AND NOT EXISTS (SELECT 1 FROM sre.agent_feedback f WHERE f.investigation_id = i.id)
        AND i.conversation_id IS NULL"""),
    ("watch_events", "DELETE FROM sre.watch_events WHERE ts < :cut"),
    ("job_runs", "DELETE FROM sre.job_runs WHERE started_at < :cut"),
    ("llm_runs", "DELETE FROM sre.llm_runs WHERE start_time < :cut"),
]


async def retention(rt, params: dict) -> dict[str, Any]:
    """Kategori başına saklama süresi Settings > Retention'dan (yoksa SRE_RETENTION_DAYS; job_runs 30 gün)."""
    from .settings import runtime as srt
    ret = (await srt.effective(rt))["retention"]
    days = {"investigations": ret["investigations_days"], "watch_events": ret["watch_events_days"],
            "llm_runs": ret["llm_runs_days"], "job_runs": ret["job_runs_days"]}
    now = datetime.now(timezone.utc)
    out: dict[str, Any] = {"retention_days": days}
    async with transaction(rt.sm) as s:
        for name, sql in RETENTION_SQL:
            res = await s.execute(text(sql), {"cut": now - timedelta(days=int(days[name]))})
            out[name] = res.rowcount or 0
        out["approvals_expired"] = await approvals.expire_due(s)
    return out


async def langsmith_sync(rt, params: dict) -> dict[str, Any]:
    from .services.langsmith_sync import sync
    return await sync(rt, params)


HANDLERS = {"health_check": health_check, "retention": retention, "langsmith_sync": langsmith_sync}


JOB_TYPES = {"watch": "watch", "health-check": "health_check", "langsmith-sync": "langsmith_sync"}


async def ensure_jobs(rt) -> None:
    """İş tanımlarını etkili ayarlardan (Settings > Scheduling; yoksa ortam değişkenleri) oluşturur/günceller."""
    from .settings import runtime as srt
    from .settings import service
    async with transaction(rt.sm) as s:
        eff = await service.effective(rt, s)
        for name, spec in srt.job_specs(rt, eff).items():
            await jobs.ensure(s, name, JOB_TYPES[name], spec["interval_seconds"], spec["params"],
                              enabled=spec["enabled"])
        await jobs.ensure(s, "retention", "retention", 3600, {})


# ---------------------------------------------------------------- döngü
async def run_due(rt, worker_id: str, handlers: dict, *, lease_s: int) -> list[dict]:
    """Vadesi gelen işleri kiralayıp çalıştırır (tek tur)."""
    done = []
    async with transaction(rt.sm) as s:
        claimed = await jobs.claim(s, worker_id, lease_s, n=5)
        todo = [(j.id, j.name, j.job_type, dict(j.params or {})) for j in claimed]
    for job_id, name, jtype, params in todo:
        fn = handlers.get(jtype)
        token = correlation_id.set(new_correlation_id())
        async with transaction(rt.sm) as s:
            run = await jobs.start_run(s, job_id, worker_id)
            run_id = run.id
        t0 = time.monotonic()
        ok, result, err = True, {}, None
        try:
            if fn is None:
                raise RuntimeError(f"bilinmeyen iş türü: {jtype}")
            result = await asyncio.wait_for(fn(rt, params), lease_s)
        except Exception as e:  # noqa: BLE001 — bir işin hatası worker'ı durdurmaz
            ok, err = False, f"{type(e).__name__}: {e}"[:2000]
            log.exception("iş başarısız", extra={"job": name})
        result = {**result, "elapsed_ms": int((time.monotonic() - t0) * 1000)}
        async with transaction(rt.sm) as s:
            await jobs.finish_run(s, run_id, job_id, worker_id, ok=ok, result=result, error=err)
        log.info("iş bitti", extra={"job": name, "ok": ok, "ms": result["elapsed_ms"]})
        correlation_id.reset(token)
        done.append({"job": name, "ok": ok, "result": result, "error": err})
    return done


async def main(ps: PlatformSettings, *, once: bool = False, runtime=None) -> int:
    from .agent.runtime import create_runtime
    from .api.app import bootstrap
    rt = runtime or await create_runtime(ps, application_name="sre-worker")
    await bootstrap(rt)
    await ensure_jobs(rt)
    # Watch işi her zaman kurulur; açık/kapalı durumu zamanlama tablosundan (Settings) gelir → yeniden başlatma gerekmez.
    watch = WatchJob(rt)
    handlers = {**HANDLERS, "watch": watch}
    wid = ps.worker_id or "worker"
    from .settings import runtime as srt
    lease = int((await srt.effective(rt))["agent"]["run_timeout_s"]) + 120
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, STOP.set)
        except (NotImplementedError, RuntimeError):
            pass
    log.info("sre-worker başladı", extra={"worker": wid, "lease_s": lease})
    try:
        while not STOP.is_set():
            try:
                await run_due(rt, wid, handlers, lease_s=lease)
            except Exception:  # noqa: BLE001 — DB kesintisi vb.: bekle ve tekrar dene
                log.exception("worker turu başarısız")
            if once:
                break
            try:
                await asyncio.wait_for(STOP.wait(), 5)
            except TimeoutError:
                pass
    finally:
        if watch:
            watch.close()
        if runtime is None:
            await rt.close()
        log.info("sre-worker durdu")
    return 0
