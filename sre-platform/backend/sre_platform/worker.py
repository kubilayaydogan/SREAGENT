"""sre-worker: zamanlanmış işler. Birden fazla worker çalışsa bile bir iş aynı anda tek worker'da çalışır:
iş satırı `FOR UPDATE SKIP LOCKED` ile kiralanır (lease) ve next_run_at ileri alınır. Worker çökerse kira süresi
dolunca iş başka worker'a geçer.

İşler:
  watch         (poll_seconds)        airlab_agent.watch durum makinesi, PG durumlu → incident + araştırma
  health_check  (SRE_HEALTH_CHECK_MINUTES) son N dakikanın tam analizi (LLM'siz) → araştırma kaydı
  retention     (saatlik)             eski kayıtların temizliği + süresi dolan onaylar
  langsmith_sync (SRE_LANGSMITH_SYNC_MINUTES) LangSmith trace/LLM özetleri → sre.llm_runs (LLM izleme panosu)
  security_ops  (60 sn)              SIEM iletimi + güvenlik alarm kuralları (varsayılan kapalı; P2.3)
"""
from __future__ import annotations

import asyncio
import logging
import os
import signal
import tempfile
import time
from datetime import datetime, timedelta, timezone
from typing import Any


from .core.config import PlatformSettings
from .core.logging import new_correlation_id, correlation_id
from .db.models import IncidentSource, InvestigationStatus, Severity, Trigger
from .db.session import transaction
from .repositories import approvals, incidents, investigations, jobs
from .repositories.base import Conflict

log = logging.getLogger("sre.worker")
STOP = asyncio.Event()


def _span(start, end) -> str:
    from airlab_agent.timefmt import span
    return span(start, end)


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
    """Health check: schedule'da seçili agent (varsayılan gömülü SRE Agent). Özel agent seçildiyse onun yayımlanmış
    sürümü zamanlanmış modda çalışır; çalıştırılamıyorsa koşu nedeniyle kaydedilir (başka agent'a geçilmez)."""
    from .builder import service as agents
    from .db.models import SYSTEM_AGENT_ID
    async with transaction(rt.sm) as s:
        aid = await agents.health_check_agent(s)
    if aid != SYSTEM_AGENT_ID:
        return await _custom_agent(rt, {"agent_id": str(aid), "created_by": "system:scheduler",
                                        "input": f"Son {int(params.get('window_minutes', 15))} dakikanın sağlık "
                                                 f"kontrolü"}, schedule_name="health-check")
    started = agents.now()
    try:
        out = await _system_health_check(rt, params)
    except Exception as e:
        await agents.record_system_run(rt.sm, run_id=f"hc-job-{started:%Y%m%d%H%M%S}", mode="scheduled",
                                       trigger="schedule", status="failed", started_at=started,
                                       schedule_name="health-check", error=f"{type(e).__name__}: {e}")
        raise
    if not out.get("skipped"):
        # run_id = investigation run_id: Agent Trace / MCP çağrıları / Context Windows bağlantıları aynı kimliği kullanır
        await agents.record_system_run(rt.sm, run_id=out.pop("run_id", None) or f"hc-job-{started:%Y%m%d%H%M%S}",
                                       mode="scheduled", trigger="schedule", status="completed", started_at=started,
                                       schedule_name="health-check", investigation_id=out.get("investigation_id"),
                                       models=out.pop("models", None))
    return out


async def _custom_agent(rt, params: dict, *, schedule_name: str) -> dict[str, Any]:
    from .builder import runs as agent_runs
    from .builder import service as agents
    from .settings import runtime as srt
    eff = await srt.effective(rt)
    try:
        srt.check_allowed(eff, "scheduled")            # platform agent kapısı tüm agent'lara uygulanır
    except srt.RunNotAllowed as e:
        from .security import audit
        await audit.record_separate(rt.sm, actor="system:scheduler", action="run.denied", section="agent",
                                    target=schedule_name, result="denied", reason_code=e.reason_code, detail=str(e))
        return {"skipped": str(e)}
    try:
        res = await agent_runs.scheduled_run(rt, agent_id=params.get("agent_id"), schedule_name=schedule_name,
                                             input_=params.get("input") or "", variables=params.get("variables") or {},
                                             created_by=params.get("created_by"))
    except agents.NotRunnable as e:
        raise RuntimeError(f"agent çalıştırılamadı ({e.code}): {e.message}") from None
    out = {"agent_run_id": res["run_id"], "agent_id": res["agent_id"], "agent_version": res["version_label"],
           "status": res["status"], "duration_ms": res["duration_ms"],
           "models": [m.get("model") for m in res.get("models") or []]}
    if res["status"] != "completed":
        e = res.get("error") or {}
        raise RuntimeError(f"agent koşusu başarısız ({e.get('code')}): {e.get('message')}"[:1500])
    return out


async def agent_run(rt, params: dict) -> dict[str, Any]:
    """Agent Builder schedule'ı (job_type=agent_run)."""
    return await _custom_agent(rt, params, schedule_name=params.get("_job_name") or "agent")


async def _system_health_check(rt, params: dict) -> dict[str, Any]:
    from airlab_agent.graph import new_run_id

    from .agent.investigation import InvestigationRunner
    ps = rt.ps
    from .settings import runtime as srt
    eff = await srt.effective(rt)
    try:
        srt.check_allowed(eff, "scheduled")
    except srt.RunNotAllowed as e:
        from .security import audit
        await audit.record_separate(rt.sm, actor="system:scheduler", action="run.denied", section="agent",
                                    target="health_check", result="denied", reason_code=e.reason_code,
                                    detail=str(e), after={"policy_version": getattr(e.decision, "policy_version", None)})
        return {"skipped": str(e)}
    lag = rt.agent.cfg["windows"]["ingest_lag_seconds"]
    end = (datetime.now(timezone.utc) - timedelta(seconds=lag)).replace(second=0, microsecond=0)
    start = end - timedelta(minutes=int(params.get("window_minutes", ps.health_check_window_minutes)))
    async with transaction(rt.sm) as s:
        inv = await investigations.create(s, run_id=new_run_id("hc"), trigger=Trigger.SCHEDULED,
                                          title=f"Zamanlanmış sağlık kontrolü {_span(start, end)}",
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
    llm_snap = (done.config_snapshot or {}).get("llm") or {}
    if done.status != InvestigationStatus.FAILED:
        out["run_id"] = done.run_id
        out["models"] = srt.models_view(llm_snap.get("effective"), ("rca",)) if (llm_snap.get("run") or {}).get("used") \
            else []
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


async def retention(rt, params: dict) -> dict[str, Any]:
    """Kategori başına saklama süresi Settings > Retention'dan (services/retention.py). Silme olduysa tek audit."""
    from .security import audit, sessions
    from .services import retention as ret_svc
    from .settings import runtime as srt
    eff = await srt.effective(rt)
    ret = eff["retention"]
    out: dict[str, Any] = {"retention_days": {k: ret[k] for k in ("investigations_days", "watch_events_days",
                                                                 "llm_runs_days", "job_runs_days", "checkpoints_days",
                                                                 "store_days")}}
    async with transaction(rt.sm) as s:
        counts = await ret_svc.purge(s, ret)
        from .memory import manager as mem_mgr          # Agent Memory: expire + arşiv/olay saklama süreleri
        counts.update({f"memory_{k}": v for k, v in (await mem_mgr.retention(s, eff.get("agent_memory", {}))).items()})
        from .middleware import queries as mw_q
        counts["middleware_executions"] = await mw_q.retention(
            s, (eff.get("agent_middleware") or {}).get("execution_retention_days", 30))
        out.update(counts)
        out["approvals_expired"] = await approvals.expire_due(s)
        out["auth_sessions_purged"] = await sessions.purge_expired(s)     # süresi 30+ gün önce dolmuş oturumlar
        deleted = {k: v for k, v in counts.items() if v}
        if deleted:
            await audit.record(s, actor="system:retention", action="retention.purge", section="retention",
                               after=deleted, detail=", ".join(f"{k}={v}" for k, v in deleted.items()))
    return out


async def langsmith_sync(rt, params: dict) -> dict[str, Any]:
    from .services.langsmith_sync import sync
    return await sync(rt, params)


HEARTBEAT = os.getenv("SRE_WORKER_HEARTBEAT", os.path.join(tempfile.gettempdir(), "sre-worker.heartbeat"))


def _heartbeat() -> None:
    """Container healthcheck'i için: başarılı her turdan sonra dosya zamanı güncellenir (DB'ye erişebilen canlı worker)."""
    try:
        with open(HEARTBEAT, "w") as f:
            f.write(datetime.now(timezone.utc).isoformat())
    except OSError:
        pass


async def security_ops(rt, params: dict) -> dict[str, Any]:
    from .services.security_ops import run
    return await run(rt, params)


HANDLERS = {"health_check": health_check, "retention": retention, "langsmith_sync": langsmith_sync,
            "security_ops": security_ops, "agent_run": agent_run}


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
        await jobs.ensure(s, "security-ops", "security_ops", 60, {})      # SIEM + alarmlar (ayar kapalıysa no-op)


# ---------------------------------------------------------------- döngü
async def run_due(rt, worker_id: str, handlers: dict, *, lease_s: int) -> list[dict]:
    """Vadesi gelen işleri kiralayıp çalıştırır (tek tur)."""
    done = []
    async with transaction(rt.sm) as s:
        claimed = await jobs.claim(s, worker_id, lease_s, n=5)
        todo = [(j.id, j.name, j.job_type, {**dict(j.params or {}), "_job_name": j.name}) for j in claimed]
    for job_id, name, jtype, params in todo:
        fn = handlers.get(jtype)
        async with transaction(rt.sm) as s:
            if not await jobs.renew(s, job_id, worker_id, lease_s):
                log.warning("iş atlandı: kira başka worker'a geçti", extra={"job": name})
                done.append({"job": name, "ok": True, "result": {"skipped": "lease_lost"}, "error": None})
                continue
            run = await jobs.start_run(s, job_id, worker_id)
            run_id = run.id
        token = correlation_id.set(new_correlation_id())
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


async def reconcile(rt, *, lease_s: int) -> dict[str, int]:
    """Süre sınırını aşmış 'çalışıyor' kayıtlarını (investigation, iş, agent çalıştırması) kapatır."""
    from .services.reconcile import close_interrupted
    from .settings import runtime as srt
    timeout = int((await srt.effective(rt))["agent"]["run_timeout_s"])
    async with transaction(rt.sm) as s:
        return await close_interrupted(s, run_timeout_s=timeout, lease_s=lease_s)


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
    last_reconcile = 0.0
    try:
        while not STOP.is_set():
            try:
                if time.monotonic() - last_reconcile >= 60:     # yarıda kalan çalıştırmalar "çalışıyor" görünmesin
                    last_reconcile = time.monotonic()
                    await reconcile(rt, lease_s=lease)
                await run_due(rt, wid, handlers, lease_s=lease)
                _heartbeat()
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
