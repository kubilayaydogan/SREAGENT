"""Faz 6 — worker: PG durumlu watch (mevcut durum makinesiyle), çift çalıştırma koruması, zamanlanmış sağlık
kontrolü, saklama, bilgi bankası tohumu, eski rapor içe aktarımı, kanıt ilişkilendirme."""
from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timedelta, timezone


from sqlalchemy import select, text  # noqa: E402

from airlab_agent.graph import run_analysis  # noqa: E402
from airlab_agent.mcp_gateway import QueryResult  # noqa: E402
from conftest import run  # noqa: E402
from scenarios import SCENARIOS  # noqa: E402
from synth import TimelineGateway, cfg  # noqa: E402
from test_agent_memory import WS, _agent_settings, gw_factory, lg_ready  # noqa: E402,F401

from sre_platform.agent.runtime import create_runtime  # noqa: E402
from sre_platform.db.models import (EvidenceType, Incident, IncidentInvestigation, IncidentSource, IncidentStatus,  # noqa: E402
                                    Investigation, InvestigationStatus, Trigger)
from sre_platform.db.session import transaction  # noqa: E402
from sre_platform.repositories import applications, incidents, investigations, jobs, knowledge  # noqa: E402

T = datetime(2026, 11, 21, 12, 0, tzinfo=timezone.utc)


async def _rt(settings, tmp_path, gw=None):
    rt = await create_runtime(settings, agent=_agent_settings(tmp_path), gateway_factory=gw or gw_factory(), llm=None)
    async with transaction(rt.sm) as s:
        await applications.sync_from_config(s, rt.agent.cfg)
    return rt


# ---------------------------------------------------------------- watch
def test_platform_watch_incident_lifecycle(settings, tmp_path, lg_ready):
    from sre_platform.services.watch_pg import WATCH_DEDUP, PgStateStore, PlatformWatcher
    normal, lock = SCENARIOS["normal"]()[1], SCENARIOS["row_lock"]()[1]
    bad: dict = {"from": None, "to": None}

    def timeline(t):
        return lock if bad["from"] and bad["from"] <= t and (bad["to"] is None or t < bad["to"]) else normal
    tl = TimelineGateway(timeline)

    async def go():
        rt = await _rt(settings, tmp_path)
        store = PgStateStore(settings.libpq_url)
        now = {"t": T}
        lag = rt.agent.cfg["windows"]["ingest_lag_seconds"]
        nxt = lambda: (now["t"] - timedelta(seconds=lag)).replace(second=0, microsecond=0)  # noqa: E731
        w = PlatformWatcher(rt, asyncio.get_running_loop(), store, clock=lambda: now["t"], gateway_factory=lambda: tl)

        async def adv(n):
            for _ in range(n):
                now["t"] += timedelta(minutes=1)
                await asyncio.to_thread(w.tick)

        async def state():
            async with transaction(rt.sm) as s:
                inc = (await s.execute(select(Incident))).scalars().all()
                links = (await s.execute(select(IncidentInvestigation.relation, Investigation.status, Investigation.trigger)
                                         .join(Investigation))).all()
                return inc, links
        try:
            assert await asyncio.to_thread(w.tick) == {"evaluated": 0, "history_filled": 15}
            await adv(3)
            assert (await state())[0] == []
            bad["from"] = nxt()
            await adv(2)
            assert (await state())[0] == []                                 # 2 pencere: onay yok
            await adv(1)
            inc, links = await state()
            assert len(inc) == 1 and inc[0].dedup_key == WATCH_DEDUP and inc[0].status == IncidentStatus.OPEN
            assert inc[0].conditions and inc[0].root_cause                  # analizden kök neden hipotezi
            assert [(r, st, tr) for r, st, tr in links] == [("trigger", InvestigationStatus.COMPLETED, Trigger.WATCH)]
            await adv(3)
            inc, links = await state()
            assert len(inc) == 1 and len(links) == 1                         # dedup: yeni incident/analiz yok
            # yeniden başlatma: aynı dakika ikinci kez sayılmaz (durum PG'de)
            store2 = PgStateStore(settings.libpq_url)
            w2 = PlatformWatcher(rt, asyncio.get_running_loop(), store2, clock=lambda: now["t"],
                                 gateway_factory=lambda: tl)
            assert (await asyncio.to_thread(w2.tick))["evaluated"] == 0
            store2.close()
            bad["to"] = nxt()
            await adv(4)
            inc, links = await state()
            assert inc[0].status == IncidentStatus.MITIGATED and inc[0].resolved_at is None  # 'Çözüldü' insan kararı
            assert sorted(r for r, *_ in links) == ["recovery", "trigger"]
            kinds = [k for (k,) in store.db.execute("SELECT kind FROM sre.watch_events ORDER BY id")]
            assert "incident_open" in kinds and "incident_recovered" in kinds
            async with transaction(rt.sm) as s:
                full = await incidents.get(s, inc[0].id, with_events=True)
                assert [e.to_status for e in full.events if e.kind == "status"] == ["OPEN", "MITIGATED"]
        finally:
            store.close()
            await rt.close()
    run(go)


# ---------------------------------------------------------------- iş kiralama
def test_jobs_never_run_twice_concurrently(settings, tmp_path, lg_ready):
    from sre_platform.worker import run_due

    async def go():
        rt = await _rt(settings, tmp_path)
        calls = []
        gate = asyncio.Event()

        async def slow(rt_, params):
            calls.append(params)
            await gate.wait()
            return {"ok": 1}
        try:
            async with transaction(rt.sm) as s:
                await jobs.ensure(s, "hc", "slowjob", 60, {"n": 1})
            t1 = asyncio.create_task(run_due(rt, "w1", {"slowjob": slow}, lease_s=30))
            await asyncio.sleep(0.5)
            r2 = await run_due(rt, "w2", {"slowjob": slow}, lease_s=30)  # aynı anda ikinci worker
            gate.set()
            r1 = await t1
            assert len(calls) == 1 and r2 == [] and r1[0]["ok"] is True
            r3 = await run_due(rt, "w2", {"slowjob": slow}, lease_s=30)   # vadesi gelmedi
            assert r3 == []
            async with transaction(rt.sm) as s:
                runs = await jobs.recent_runs(s)
                assert [(r.worker_id, r.status.value) for r in runs] == [("w1", "SUCCEEDED")]
        finally:
            await rt.close()
    run(go)


def test_health_check_and_retention(settings, tmp_path, lg_ready):
    from sre_platform.worker import health_check, retention

    async def go():
        rt = await _rt(settings, tmp_path)
        try:
            r = await health_check(rt, {"window_minutes": 15})
            assert r["status"] in ("COMPLETED", "PARTIAL")
            async with transaction(rt.sm) as s:
                inv = await investigations.get(s, uuid.UUID(r["investigation_id"]), with_children=True)
                assert inv.trigger == Trigger.SCHEDULED and inv.tools
                old_free = await investigations.create(s, run_id="old-1", trigger=Trigger.MANUAL, title="eski",
                                                       window_start=T - timedelta(days=400),
                                                       window_end=T - timedelta(days=400, minutes=-15))
                old_kept = await investigations.create(s, run_id="old-2", trigger=Trigger.MANUAL, title="eski incident",
                                                       window_start=T - timedelta(days=400),
                                                       window_end=T - timedelta(days=400, minutes=-15))
                inc = await incidents.create(s, title="eski olay", source=IncidentSource.MANUAL)
                await incidents.link_investigation(s, inc.id, old_kept.id)
                await s.execute(text("UPDATE sre.investigations SET created_at = now() - interval '400 days' "
                                     "WHERE run_id IN ('old-1','old-2')"))
                ids = (old_free.id, old_kept.id)
            out = await retention(rt, {"days": 180})
            assert out["investigations"] == 1
            async with transaction(rt.sm) as s:
                left = {i for (i,) in (await s.execute(select(Investigation.id).where(Investigation.id.in_(ids)))).all()}
                assert left == {ids[1]}                                      # incident'a bağlı olan saklandı
        finally:
            await rt.close()
    run(go)


# ---------------------------------------------------------------- tohum / içe aktarma
def test_seed_is_idempotent_and_recommends(adb):
    from sre_platform.seed import load_seed, seed_knowledge
    items = load_seed()
    assert len(items["known_issues"]) == 8 and len(items["runbooks"]) >= 4

    async def fn(s):
        a = await seed_knowledge(s)
        b = await seed_knowledge(s)
        rec = await knowledge.recommend(s, mechanisms=["row_lock"], apps=["checkin-service"])
        return a, b, rec
    a, b, rec = run(adb(fn))
    assert a == {"known_issues": len(items["known_issues"]), "runbooks": len(items["runbooks"])} and b == {"known_issues": 0, "runbooks": 0}
    assert rec["known_issues"][0].mechanism == "row_lock"
    titles = {r.title: r for r in rec["runbooks"]}
    assert titles["Bloklayan oturumu sonlandırma"].requires_approval is True    # yüksek risk → onay zorunlu


def test_import_legacy_reports(settings, tmp_path, adb):
    import json

    from sre_platform.seed import import_reports
    st = run_analysis(_agent_settings(tmp_path), gw_factory(), WS[1].start, WS[1].end, use_llm=False,
                      run_id="inc-20261121-000000-abcdef-open", mode="watch-incident", persist_files=False)
    d = tmp_path / "legacy-reports"
    d.mkdir()
    (d / "a.json").write_text(json.dumps(st["report"], default=str), encoding="utf-8")
    (d / "bozuk.json").write_text("{", encoding="utf-8")
    n1 = run(import_reports, settings, str(d))
    n2 = run(import_reports, settings, str(d))
    assert (n1, n2) == (1, 0)

    async def fn(s):
        inv = await investigations.by_run_id(s, "inc-20261121-000000-abcdef-open")
        return inv, (await investigations.get(s, inv.id, with_children=True)).evidence
    inv, ev = run(adb(fn))
    assert inv.trigger == Trigger.IMPORT and inv.primary_mechanism == "row_lock" and inv.status == InvestigationStatus.COMPLETED
    assert inv.created_at == datetime.fromisoformat(st["report"]["generated_at"].replace("Z", "+00:00"))
    assert any(e.evidence_type == EvidenceType.METRIC for e in ev)


# ---------------------------------------------------------------- kanıt ilişkilendirme
def test_correlation_evidence_mapping():
    from sre_platform.services.correlation import evidence, queries
    end = T
    qs = dict(queries(T - timedelta(minutes=15), end, cfg()))
    assert "chaos-controller" not in qs["slow_traces"] and 'spring.application.name == "checkin-service"' in qs["slow_traces"]
    res = {
        "slow_traces": QueryResult("slow_traces", qs["slow_traces"], "OK", [
            {"start_time": "2026-11-21T11:55:00Z", "trace.id": "abc123", "span.id": "s1", "app": "checkin-service",
             "endpoint": "/checkin", "method": "POST", "status": 200, "duration_ms": 1234.5},
            {"trace.id": None}], query_id="q-7"),
        "problems": QueryResult("problems", qs["problems"], "EMPTY", []),
    }
    ev = evidence(res, end)
    tr = [e for e in ev if e["type"] == EvidenceType.TRACE and e.get("trace_id")]
    assert len(tr) == 1 and tr[0]["trace_id"] == "abc123" and tr[0]["span_id"] == "s1"
    assert "1234 ms" in tr[0]["content"] or "1235 ms" in tr[0]["content"]
    assert tr[0]["dynatrace_query_id"] == "q-7"
    pb = [e for e in ev if e["signal"] == "problems"]
    assert len(pb) == 1 and pb[0]["data_status"] == "EMPTY" and "kayıt yok" in pb[0]["content"]  # sahte problem yok

