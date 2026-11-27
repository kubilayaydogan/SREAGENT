"""Faz 2 — şema, migration ve repository testleri (gerçek PostgreSQL)."""
from __future__ import annotations

import asyncio
import json
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy.exc import IntegrityError

from conftest import run
from sre_platform.db.migrate import compare_type
from sre_platform.db.models import (ActionType, Base, IncidentSource, IncidentStatus, InvestigationStatus, MessageRole,
                                    Role, Severity, Trigger, Verdict)
from sre_platform.repositories import (applications, approvals, conversations, incidents, investigations, jobs,
                                       knowledge, rca, users)
from sre_platform.repositories.base import Conflict, InvalidTransition, NotFound

ROOT = Path(__file__).resolve().parents[3]
E2 = json.loads((ROOT / "docs/platform/examples/sre-agent/real-E2-row-lock.json").read_text(encoding="utf-8"))
NOW = datetime.now(timezone.utc)


# ---------------------------------------------------------------- migration
def test_models_match_migration(db_url):
    """Model ↔ migration farkı olmamalı (autogenerate boş çıkmalı)."""
    eng = sa.create_engine(db_url, connect_args={"options": "-c search_path=public"})
    with eng.connect() as c:
        mc = MigrationContext.configure(c, opts={"include_schemas": True, "version_table_schema": "sre",
                                                 "include_object": lambda o, n, t, r, ct: t != "table" or o.schema == "sre",
                                                 "compare_type": compare_type})
        diff = compare_metadata(mc, Base.metadata)
    eng.dispose()
    assert diff == [], diff


def test_downgrade_and_reupgrade(db_url):
    from sre_platform.db import migrate
    migrate.downgrade(db_url, "base")
    assert migrate.current_revision(db_url) is None
    migrate.upgrade(db_url)
    assert migrate.check_ready(db_url)[0]


def test_langgraph_setup_idempotent(db_url):
    from sre_platform.db import migrate
    libpq = db_url.replace("postgresql+psycopg://", "postgresql://")
    migrate.setup_langgraph(libpq)
    migrate.setup_langgraph(libpq)
    eng = sa.create_engine(db_url)
    with eng.connect() as c:
        t = {r[0] for r in c.execute(sa.text("select tablename from pg_tables where schemaname='langgraph'"))}
    eng.dispose()
    assert {"checkpoints", "checkpoint_blobs", "checkpoint_writes", "store"} <= t


# ---------------------------------------------------------------- kullanıcı / uygulama
def test_users_and_auth(adb):
    async def fn(s):
        assert await users.ensure_admin(s, "admin", "cok-gizli-parola-1")
        assert not await users.ensure_admin(s, "admin2", "baska-parola-123")
        u = await users.authenticate(s, "ADMIN", "cok-gizli-parola-1")
        assert u and u.role == Role.ADMIN and u.last_login_at
        assert await users.authenticate(s, "admin", "yanlis-parola-xx") is None
        assert await users.authenticate(s, "yok", "cok-gizli-parola-1") is None
        assert "cok-gizli" not in u.password_hash
        with pytest.raises(Conflict):
            await users.create(s, "admin", "x", Role.SRE, "baska-parola-123")
    run(adb(fn))


def test_short_password_rejected(adb):
    async def fn(s):
        with pytest.raises(ValueError):
            await users.create(s, "kisa", "x", Role.VIEWER, "123")
    run(adb(fn))


def test_applications_sync(adb):
    from airlab_agent.config import load_yaml_config

    async def fn(s):
        n = await applications.sync_from_config(s, load_yaml_config())
        n2 = await applications.sync_from_config(s, load_yaml_config())
        apps = await applications.list_(s, "business")
        return n, n2, [a.id for a in apps], (await applications.get(s, "reservation-service")).db_role
    n, n2, ids, role = run(adb(fn))
    assert n == n2 == 7 and len(ids) == 4 and role == "svc_reservation"


# ---------------------------------------------------------------- konuşma
def test_conversations_isolated_per_user_and_fts(adb):
    async def fn(s):
        a = await users.create(s, "ayse", "Ayşe", Role.SRE, "parola-ayse-123")
        b = await users.create(s, "berk", "Berk", Role.SRE, "parola-berk-123")
        c = await conversations.create(s, a.id, "Reservation yavaşlığı")
        await conversations.add_message(s, c.id, MessageRole.USER, "Reservation uygulaması neden yavaşladı?")
        await conversations.add_message(s, c.id, MessageRole.ASSISTANT,
                                        "Satır kilitleri bekleniyor; bağlantı havuzu doldu.", {"k": 1})
        with pytest.raises(NotFound):
            await conversations.get(s, c.id, b.id)
        assert (await conversations.list_(s, b.id)).total == 0
        hits = await conversations.search_messages(s, a.id, "kilit havuz")          # Türkçe kök: kilitleri → kilit
        assert hits and "kilit" in hits[0][0].content
        assert await conversations.search_messages(s, b.id, "kilit") == []
        assert (await conversations.list_(s, a.id, q="havuz")).total == 1
        assert (await conversations.list_(s, a.id, q="100%_")).total == 0          # LIKE joker kaçışı
        msgs = await conversations.recent_messages(s, c.id, 1)
        assert msgs[0].role == MessageRole.ASSISTANT
    run(adb(fn))


def test_secret_redacted_before_store(adb):
    from airlab_agent.redact import register_secrets
    register_secrets(["supersecretvalue123"])

    async def fn(s):
        u = await users.create(s, "c", "C", Role.SRE, "parola-c-12345")
        c = await conversations.create(s, u.id, "t")
        m = await conversations.add_message(s, c.id, MessageRole.USER,
                                            "token dt0c01.ABCDEFGHIJKLMNOPQRSTUVWX.ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789ABCDEFGHIJKLMNOPQRSTUV supersecretvalue123")
        return m.content
    out = run(adb(fn))
    assert "supersecretvalue123" not in out and "ABCDEFGHIJKLMNOPQRSTUVWX" not in out


# ---------------------------------------------------------------- araştırma
async def _inv_from_e2(s, **kw):
    w = {x["name"]: x for x in E2["windows"]}["window"]
    inv = await investigations.create(s, run_id=kw.get("run_id", "rep-" + uuid.uuid4().hex[:8]), trigger=Trigger.MANUAL,
                                      title="E2 satır kilidi",
                                      window_start=datetime.fromisoformat(w["start"].replace("Z", "+00:00")),
                                      window_end=datetime.fromisoformat(w["end"].replace("Z", "+00:00")))
    await investigations.mark_running(s, inv)
    return inv


def test_investigation_from_real_report(adb):
    async def fn(s):
        inv = await _inv_from_e2(s)
        t = await investigations.tool_started(s, inv.id, "execute-dql", step="collect", signal="http",
                                              arguments={"dqlQueryString": "timeseries ..."})
        await investigations.tool_finished(s, t, ok=True, result_status="OK", record_count=8, query_id="q1",
                                           evidence_key="ev-0526fc3b03")
        await investigations.record_tool(s, inv.id, "execute-dql", ok=False, signal="spans", error="timeout",
                                         duration_ms=60000)
        await investigations.complete(s, inv, E2)
        await investigations.complete(s, inv, E2)                       # idempotent (kanıt upsert)
        full = await investigations.get(s, inv.id, with_children=True)
        return full
    inv = run(adb(fn))
    assert inv.status == InvestigationStatus.COMPLETED
    assert inv.severity == Severity.HIGH and float(inv.risk_score) == 65.1
    assert inv.primary_mechanism == "row_lock" and inv.source_apps == ["reservation-service"]
    assert inv.affected_apps == ["checkin-service"] and inv.confidence.value == "high"
    assert inv.root_cause_hypothesis.startswith("Satır kilidi")
    assert len(inv.evidence) == len(E2["evidence"])
    edges = next(e for e in inv.evidence if e.signal == "edges")
    assert edges.evidence_type.value == "LOG" and "row_lock" in edges.content and edges.query_reference
    spans = next(e for e in inv.evidence if e.signal == "spans")
    assert spans.evidence_type.value == "TRACE"
    assert [t.execution_status.value for t in inv.tools] == ["SUCCEEDED", "FAILED"]
    assert inv.tools[1].error_message == "timeout" and inv.duration_ms is not None
    assert all(not isinstance(e.get("records"), list) for e in inv.report["evidence"])


def test_investigation_filters_search_similar(adb):
    async def fn(s):
        a = await _inv_from_e2(s)
        await investigations.complete(s, a, E2)
        b = await investigations.create(s, run_id="rep-b", trigger=Trigger.CHAT, title="Baggage yük artışı",
                                        description="baggage bağımsız yük", window_start=NOW - timedelta(minutes=10),
                                        window_end=NOW)
        await investigations.fail(s, b, "MCP erişilemedi")
        r1 = await investigations.list_(s, application_id="checkin-service")
        r2 = await investigations.list_(s, status=[InvestigationStatus.FAILED])
        r3 = await investigations.list_(s, q="satır kilidi")
        r4 = await investigations.list_(s, severity=[Severity.HIGH], sort="-risk_score")
        with pytest.raises(ValueError):
            await investigations.list_(s, sort="password_hash")
        sim = await investigations.similar(s, mechanism="row_lock", apps=["checkin-service"], exclude_id=uuid.uuid4())
        st = await investigations.stats(s, NOW - timedelta(days=1))
        return r1.total, r2.items[0].id == b.id, r3.total, r4.total, [x.id for x in sim] == [a.id], st
    t1, failed_ok, t3, t4, sim_ok, st = run(adb(fn))
    assert (t1, failed_ok, t3, t4, sim_ok) == (1, True, 1, 1, True)
    assert st["total"] == 2 and st["by_status"]["FAILED"] == 1


def test_run_id_unique(adb):
    async def fn(s):
        await _inv_from_e2(s, run_id="dup")
        await _inv_from_e2(s, run_id="dup")
    with pytest.raises(IntegrityError):
        run(adb(fn))


# ---------------------------------------------------------------- incident
def test_incident_lifecycle_and_timeline(adb):
    async def fn(s):
        u = await users.create(s, "sre1", "SRE", Role.SRE, "parola-sre-1234")
        inv = await _inv_from_e2(s)
        inc = await incidents.create(s, title="checkin gecikmesi", source=IncidentSource.WATCH, severity=Severity.HIGH,
                                     dedup_key="latency:checkin-service", investigation_id=inv.id,
                                     conditions=["latency:checkin-service"])
        with pytest.raises(Conflict):
            await incidents.create(s, title="tekrar", source=IncidentSource.WATCH, dedup_key="latency:checkin-service")
        await incidents.transition(s, inc, IncidentStatus.INVESTIGATING, user_id=u.id, note="bakıyorum")
        await incidents.transition(s, inc, IncidentStatus.MITIGATED, user_id=u.id)
        await incidents.transition(s, inc, IncidentStatus.RESOLVED, user_id=u.id)
        with pytest.raises(InvalidTransition):
            await incidents.transition(s, inc, IncidentStatus.MITIGATED)
        # çözülen incident'tan sonra aynı anahtarla yenisi açılabilir
        inc2 = await incidents.create(s, title="yeniden", source=IncidentSource.WATCH, dedup_key="latency:checkin-service")
        # eskiyi yeniden açmak, açık olan varken çakışır
        with pytest.raises(Conflict):
            await incidents.transition(s, inc, IncidentStatus.INVESTIGATING)
        await incidents.update_fields(s, inc2, root_cause="hot row", recommended_actions=["transaction kısalt"])
        await incidents.link_investigation(s, inc2.id, inv.id)
        await incidents.link_investigation(s, inc2.id, inv.id)          # tekrar bağlama sessizce yok sayılır
        full = await incidents.get(s, inc.id, with_events=True)
        linked = await incidents.investigations_of(s, inc2.id)
        lst = await incidents.list_(s, status=[IncidentStatus.OPEN])
        return full, linked, lst, await incidents.counts(s), inc2
    full, linked, lst, counts, inc2 = run(adb(fn))
    assert full.status == IncidentStatus.RESOLVED and full.acknowledged_at and full.mitigated_at and full.resolved_at
    assert [e.to_status for e in full.events if e.kind == "status"] == ["OPEN", "INVESTIGATING", "MITIGATED", "RESOLVED"]
    assert full.events[1].message == "bakıyorum"
    assert len(linked) == 1 and lst.total == 1 and counts == {"RESOLVED": 1, "OPEN": 1}
    assert inc2.root_cause == "hot row"


# ---------------------------------------------------------------- RCA / geri bildirim
def test_rca_and_feedback(adb):
    async def fn(s):
        u1 = await users.create(s, "u1", "U1", Role.SRE, "parola-u1-12345")
        inv = await _inv_from_e2(s)
        r = await rca.create(s, inv.id, {"title": "RCA: checkin gecikmesi", "summary": "reservation hot-row kilidi",
                                         "suspected_root_cause": "uzun transaction", "markdown": "# RCA",
                                         "confidence": "high", "contributing_factors": ["havuz 8"],
                                         "supporting_evidence": [{"key": "ev-1", "type": "LOG"}]})
        f1 = await rca.upsert_feedback(s, inv_id=inv.id, user_id=u1.id, verdict=Verdict.PARTIALLY_CORRECT, rating=3)
        f2 = await rca.upsert_feedback(s, inv_id=inv.id, user_id=u1.id, verdict=Verdict.CORRECT, rating=5,
                                       comment="doğru", rca_id=r.id)
        lst = await rca.list_(s, q="transaction")
        return r, f1.id == f2.id, f2, await rca.feedback_summary(s), lst.total, await rca.latest_for(s, inv.id)
    r, same, f2, summ, n, latest = run(adb(fn))
    assert same and f2.is_correct and f2.verdict == Verdict.CORRECT and summ == {"CORRECT": 1}
    assert n == 1 and latest.id == r.id


def test_feedback_rating_range(adb):
    async def fn(s):
        u = await users.create(s, "u2", "U2", Role.SRE, "parola-u2-12345")
        inv = await _inv_from_e2(s)
        await rca.upsert_feedback(s, inv_id=inv.id, user_id=u.id, verdict=Verdict.INCORRECT, rating=9)
    with pytest.raises(IntegrityError):
        run(adb(fn))


# ---------------------------------------------------------------- bilgi bankası
def test_knowledge_seed_and_recommend(adb):
    async def fn(s):
        await applications.sync_from_config(s, {"apps": ["checkin-service"], "tooling_apps": []})
        items = {"known_issues": [{"title": "Hot-row satır kilidi", "mechanism": "row_lock", "origin": "seed",
                                   "symptoms": "checkin gecikmesi, Lock/transactionid beklemesi",
                                   "resolution": "transaction sınırlarını kısalt"}],
                 "runbooks": [{"title": "Uzun transaction sonlandırma", "mechanism": "row_lock", "origin": "seed",
                               "procedure": "1. pg_stat_activity ...", "risk_level": "high"}]}
        n1 = await knowledge.seed(s, items)
        n2 = await knowledge.seed(s, items)
        rec = await knowledge.recommend(s, mechanisms=["row_lock"], apps=["checkin-service"])
        found = await knowledge.list_known_issues(s, q="satır kilidi")
        return n1, n2, rec, found.total
    n1, n2, rec, found = run(adb(fn))
    assert n1 == {"known_issues": 1, "runbooks": 1} and n2 == {"known_issues": 0, "runbooks": 0}
    assert rec["runbooks"][0].requires_approval is True                   # yüksek risk → onay zorunlu
    assert rec["known_issues"][0].mechanism == "row_lock" and found == 1


# ---------------------------------------------------------------- onay
def test_approval_four_eyes(adb):
    async def fn(s):
        a = await users.create(s, "req", "R", Role.SRE, "parola-req-1234")
        b = await users.create(s, "appr", "A", Role.ADMIN, "parola-appr-123")
        req = await approvals.request(s, action_type=ActionType.RESTART, target="checkin-service",
                                      justification="havuz kilitlendi", requested_by=a.id)
        with pytest.raises(Conflict):
            await approvals.decide(s, req.id, approve=True, decided_by=a.id)
        ok = await approvals.decide(s, req.id, approve=True, decided_by=b.id, comment="tamam")
        with pytest.raises(InvalidTransition):
            await approvals.decide(s, req.id, approve=False, decided_by=b.id)
        old = await approvals.request(s, action_type=ActionType.SQL_WRITE, target="db", justification="x",
                                      requested_by=a.id, ttl_hours=-1)
        n = await approvals.expire_due(s)
        return ok.status.value, n, (await approvals.get(s, old.id)).status.value
    assert run(adb(fn)) == ("APPROVED", 1, "EXPIRED")


# ---------------------------------------------------------------- zamanlanmış işler
def test_job_claim_is_exclusive(settings, adb):
    async def setup(s):
        await jobs.ensure(s, "watch", "watch", 60)
        await jobs.ensure(s, "health", "health_check", 300)
    run(adb(setup))

    from sre_platform.db.session import make_engine, make_sessionmaker

    async def race():
        eng = make_engine(settings, "pytest-race")
        sm = make_sessionmaker(eng)
        gate = asyncio.Event()

        async def worker(name):
            async with sm() as s:
                async with s.begin():
                    got = await jobs.claim(s, name, lease_seconds=120, n=5)
                    await gate.wait()                  # kilitleri tutarken diğer worker da denesin
                    return name, [j.name for j in got]

        t = [asyncio.create_task(worker(f"w{i}")) for i in range(4)]
        await asyncio.sleep(0.5)
        gate.set()
        res = await asyncio.gather(*t)
        # ikinci tur: kira dolmadı → kimse alamaz
        async with sm() as s:
            async with s.begin():
                again = await jobs.claim(s, "w9", 120, 5)
        await eng.dispose()
        return res, again
    res, again = run(race)
    claimed = [n for _, names in res for n in names]
    assert sorted(claimed) == ["health", "watch"]           # her iş tam bir kez
    assert again == []

    async def finish(s):
        js = await jobs.list_jobs(s)
        w = next(j for j in js if j.name == "watch")
        r = await jobs.start_run(s, w.id, w.locked_by)
        await jobs.finish_run(s, r.id, w.id, w.locked_by, ok=True, result={"windows": 1})
        await s.flush()
        await s.refresh(w)
        return w.locked_by, w.next_run_at > datetime.now(timezone.utc), (await jobs.recent_runs(s, w.id))[0].status.value
    assert run(adb(finish)) == (None, True, "SUCCEEDED")
