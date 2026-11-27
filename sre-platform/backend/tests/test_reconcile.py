"""Scheduler ve yarıda kalan çalıştırmalar: aynı iş iki kez çalışmaz; süreç ortasında kesilen çalıştırma
"çalışıyor" görünmeye devam etmez (gerçek PostgreSQL)."""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone


from sqlalchemy import text  # noqa: E402

from conftest import run  # noqa: E402
from test_agent_memory import lg_ready  # noqa: E402,F401
from test_worker import _rt  # noqa: E402

from sre_platform.db.models import InvestigationStatus, JobStatus, ToolStatus, Trigger  # noqa: E402
from sre_platform.db.session import transaction  # noqa: E402
from sre_platform.repositories import investigations, jobs  # noqa: E402
from sre_platform.services.reconcile import INTERRUPTED, close_interrupted  # noqa: E402

NOW = datetime.now(timezone.utc)


def test_job_whose_lease_moved_to_another_worker_is_not_run(settings, tmp_path, lg_ready):
    """Bir turda iki iş kiralanır. İlki sürerken ikincinin kirası başka worker'a geçerse ikinci iş çalıştırılmaz."""
    from sre_platform.worker import run_due

    async def go():
        rt = await _rt(settings, tmp_path)
        calls: list[str] = []
        gate, entered = asyncio.Event(), asyncio.Event()

        async def job(rt_, params):
            calls.append(params["_job_name"])
            entered.set()
            await gate.wait()
            return {"ok": 1}
        try:
            async with transaction(rt.sm) as s:
                await jobs.ensure(s, "first", "j", 60, {})
                await jobs.ensure(s, "second", "j", 60, {})
            t1 = asyncio.create_task(run_due(rt, "w1", {"j": job}, lease_s=30))
            await asyncio.wait_for(entered.wait(), 10)
            running = calls[0]
            async with transaction(rt.sm) as s:             # kira doldu ve diğer işi w2 aldı (simülasyon)
                await s.execute(text("UPDATE sre.scheduled_jobs SET locked_by = 'w2' WHERE name <> :n"), {"n": running})
            gate.set()
            done = await t1
            assert calls == [running]
            skipped = [d for d in done if d["job"] != running]
            assert skipped and skipped[0]["result"] == {"skipped": "lease_lost"}
            async with transaction(rt.sm) as s:
                runs = await jobs.recent_runs(s)
                assert len(runs) == 1 and runs[0].status == JobStatus.SUCCEEDED
        finally:
            await rt.close()
    run(go)


def test_interrupted_runs_are_closed_and_live_ones_untouched(settings, tmp_path, lg_ready):
    """Süre sınırını aşmış RUNNING kayıt FAILED olur ve nedeni yazılır; süren (yeni) çalıştırmaya dokunulmaz."""
    async def go():
        rt = await _rt(settings, tmp_path)
        try:
            async with transaction(rt.sm) as s:
                old = await investigations.create(s, run_id="man-old", trigger=Trigger.MANUAL, title="eski",
                                                  window_start=NOW - timedelta(hours=3), window_end=NOW - timedelta(hours=2))
                await investigations.mark_running(s, old)
                tool = await investigations.tool_started(s, old.id, "execute-dql", step="collect", signal="http")
                live = await investigations.create(s, run_id="man-live", trigger=Trigger.MANUAL, title="yeni",
                                                   window_start=NOW - timedelta(minutes=15), window_end=NOW)
                await investigations.mark_running(s, live)
                await jobs.ensure(s, "hc", "j", 60, {})
                (job,) = await jobs.claim(s, "w1", 30)
                jr_old = await jobs.start_run(s, job.id, "w1")
                jr_live = await jobs.start_run(s, job.id, "w1")
                old_id, live_id, tool_id, jr_old_id, jr_live_id = old.id, live.id, tool.id, jr_old.id, jr_live.id
                await s.execute(text("UPDATE sre.investigations SET created_at = now() - interval '2 hours', "
                                     "started_at = now() - interval '2 hours' WHERE id = :i"), {"i": old_id})
                await s.execute(text("UPDATE sre.job_runs SET started_at = now() - interval '2 hours' WHERE id = :i"),
                                {"i": jr_old_id})
            async with transaction(rt.sm) as s:
                out = await close_interrupted(s, run_timeout_s=300, lease_s=420)
            assert out == {"investigations": 1, "tool_executions": 1, "job_runs": 1, "agent_runs": 0}
            async with transaction(rt.sm) as s:
                o, lv = await investigations.get(s, old_id), await investigations.get(s, live_id)
                assert o.status == InvestigationStatus.FAILED and o.error == INTERRUPTED and o.completed_at is not None
                assert o.risk_score is None and o.report is None            # sonuç uydurulmaz
                assert lv.status == InvestigationStatus.RUNNING and lv.error is None
                from sre_platform.db.models import JobRun, ToolExecution
                assert (await s.get(ToolExecution, tool_id)).execution_status == ToolStatus.FAILED
                assert (await s.get(JobRun, jr_old_id)).status == JobStatus.FAILED
                assert (await s.get(JobRun, jr_live_id)).status == JobStatus.RUNNING
            async with transaction(rt.sm) as s:                              # ikinci tur: yapılacak iş yok
                assert not any((await close_interrupted(s, run_timeout_s=300, lease_s=420)).values())
        finally:
            await rt.close()
    run(go)
