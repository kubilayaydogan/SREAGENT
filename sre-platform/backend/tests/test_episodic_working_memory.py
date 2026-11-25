"""Episodic memory (agent_episodes) ve working memory (focus + digests).

Birim testleri DB gerektirmez; akış testleri gerçek PostgreSQL + sahte MCP/Claude kullanır (test_agent_memory ile aynı
düzen)."""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import sqlalchemy as sa

from conftest import run
from sre_platform.agent import working_memory as wm
from sre_platform.agent.chat import MAX_STATE_MESSAGES, ChatOrchestrator, TurnContext
from sre_platform.agent.events import ListEmitter
from sre_platform.db.models import AgentEpisode, IncidentSource, IncidentStatus, Verdict
from sre_platform.db.session import transaction
from sre_platform.repositories import episodes, incidents, investigations, rca
from test_agent_memory import FakePort, _ctx, _setup, gw_factory, lg_ready  # noqa: F401  (fixture yeniden kullanımı)

ROW_LOCK_REPORT = {
    "impacted": {"reservation-service": {"reasons": ["latency ×22.9 (http_mean (tail), +71 ms)"]},
                 "baggage-service": {"reasons": ["latency ×3.0 (http_mean, +40 ms)", "5xx rate 0.1% → 4.0%",
                                                 "pool timeout 3"]}},
    "causality": {"primary_mechanism": "row_lock", "source_apps": ["checkin-service"]},
    "mechanisms": [{"id": "row_lock", "edge_pairs": [
        {"n": 8, "blocker_app": "checkin-service", "waiter_app": "reservation-service"},
        {"n": 11, "blocker_app": "baggage-service", "waiter_app": "baggage-service"}]}, {"id": "pool_saturation"}],
}


# ---------------------------------------------------------------- birim: episode
def test_fingerprint_is_deterministic_and_wording_free():
    fp = episodes.fingerprint(ROW_LOCK_REPORT)
    assert fp["symptoms"] == ["baggage-service:errors", "baggage-service:latency", "baggage-service:pool_timeout",
                              "lock:checkin-service>reservation-service", "reservation-service:latency",
                              "source:checkin-service"]
    assert fp["mechanism"] == "row_lock" and fp["mechanisms"] == ["pool_saturation", "row_lock"]
    assert episodes.fingerprint(ROW_LOCK_REPORT) == fp                          # aynı rapor → aynı etiketler
    assert episodes.fingerprint({}) == {"symptoms": [], "mechanisms": [], "mechanism": None}


def test_similarity_weights_and_outcome():
    a = {**episodes.fingerprint(ROW_LOCK_REPORT), "apps": ["reservation-service", "checkin-service"]}
    assert episodes.similarity(a, a)[0] == 1.0
    other = {"symptoms": ["db-vm:cpu"], "mechanism": "db_cpu", "mechanisms": ["db_cpu"], "apps": ["x"]}
    assert episodes.similarity(a, other)[0] == 0.0
    partial = {"symptoms": ["reservation-service:latency"], "mechanism": "row_lock", "mechanisms": ["row_lock"],
               "apps": ["reservation-service"]}
    sc, shared = episodes.similarity(a, partial)
    assert shared == ["reservation-service:latency"] and episodes.MIN_SIMILARITY < sc < 1
    C, P, I = Verdict.CORRECT, Verdict.PARTIALLY_CORRECT, Verdict.INCORRECT
    assert episodes.outcome_of([])[0] == "unverified"
    assert episodes.outcome_of([C, C, I])[0] == "confirmed"
    assert episodes.outcome_of([I, I, C])[0] == "rejected"
    assert episodes.outcome_of([P])[0] == "partially_confirmed"
    assert episodes.outcome_of([C, I])[0] == "partially_confirmed"            # eşitlik: kesin değil


# ---------------------------------------------------------------- birim: working memory
FOCUS = {"apps": ["reservation-service"], "mechanism": "row_lock",
         "window": {"start": "2026-11-25T10:00:00Z", "end": "2026-11-25T10:30:00Z"}, "investigation": "inv:x"}


def _r(intent="analyze", **kw):
    return {"intent": intent, "focus_apps": [], "minutes": None, "start_utc": None, "end_utc": None,
            "mechanism": None, **kw}


def test_resolve_inherits_only_missing_fields_for_elliptic_questions():
    d, notes = wm.resolve(_r(), FOCUS, "Peki aynı pencerede tekrar bak", explicit_window=False)
    assert d["focus_apps"] == ["reservation-service"]
    assert (d["start_utc"], d["end_utc"]) == ("2026-11-25T10:00:00Z", "2026-11-25T10:30:00Z") and len(notes) == 2
    d, _ = wm.resolve(_r(), FOCUS, "peki 1 saat öncesi?", explicit_window=False)
    assert (d["start_utc"], d["end_utc"]) == ("2026-11-25T09:00:00Z", "2026-11-25T09:30:00Z")
    d, _ = wm.resolve(_r(), FOCUS, "Peki bir saat öncesine bak", explicit_window=False)
    assert d["start_utc"] == "2026-11-25T09:00:00Z"
    # açık bilgi odaktan önce gelir
    d, notes = wm.resolve(_r(focus_apps=["checkin-service"], minutes=15), FOCUS, "peki checkin son 15 dakika",
                          explicit_window=False)
    assert d["focus_apps"] == ["checkin-service"] and d["minutes"] == 15 and d["start_utc"] is None and not notes
    # arayüzde seçilen aralık → pencere devralınmaz
    d, notes = wm.resolve(_r(), FOCUS, "peki 1 saat öncesi?", explicit_window=True)
    assert d["start_utc"] is None and notes == ["uygulama: reservation-service"]
    # bağımsız yeni soru odak devralmaz
    d, notes = wm.resolve(_r(), FOCUS, "Sistem genelinde son 30 dakikayı analiz et", explicit_window=False)
    assert d["focus_apps"] == [] and not notes
    assert wm.resolve(_r(), None, "peki?", explicit_window=False) == (_r(), [])
    # geçmiş sorusu: mechanism devralınır, pencere değil
    d, _ = wm.resolve(_r("history"), FOCUS, "bunun benzeri daha önce oldu mu?", explicit_window=False)
    assert d["mechanism"] == "row_lock" and d["start_utc"] is None


def test_digests_are_capped_and_turns_monotonic():
    ds = []
    for i in range(1, wm.MAX_DIGESTS + 6):
        ds = wm.append_digests(ds, [wm.digest(wm.next_turn({"digests": ds}), f"soru {i}", _r(), None, "x" * 500)])
    assert len(ds) == wm.MAX_DIGESTS and ds[-1]["turn"] == wm.MAX_DIGESTS + 5
    assert ds[0]["question"] == "soru 6" and ds[-1]["answer_head"].endswith("…")
    assert len(ds[-1]["answer_head"]) == wm.DIGEST_ANSWER_CHARS + 1
    assert wm.router_hint(FOCUS).startswith("uygulamalar=reservation-service") and wm.router_hint(None) is None


# ---------------------------------------------------------------- akış: episodic memory
def _count(settings, sql, **p):
    eng = sa.create_engine(settings.database_url)
    with eng.connect() as c:
        n = c.execute(sa.text(sql), p).scalar()
    eng.dispose()
    return n


def test_episode_lifecycle_feedback_incident_and_recall(settings, lg_ready, tmp_path):
    async def go():
        port = FakePort()
        rt, uid, cid, tid = await _setup(settings, tmp_path, llm=port)
        try:
            o = ChatOrchestrator(rt)
            ctx, _ = _ctx(uid, cid, tid)
            first = await o.turn(ctx, "Checkin neden yavaşladı?")
            inv_id = uuid.UUID(first["investigation"]["investigation_id"])
            async with transaction(rt.sm) as s:
                ep0 = await s.get(AgentEpisode, inv_id)
                snap0 = (ep0.outcome, list(ep0.symptoms), ep0.mechanism, ep0.resolution)
                # kullanıcı hipotezi yanlış buldu
                await rca.upsert_feedback(s, inv_id=inv_id, user_id=uid, verdict=Verdict.INCORRECT,
                                          comment="asıl sebep pool ayarıydı")
                ep1 = await s.get(AgentEpisode, inv_id, populate_existing=True)
                snap1 = (ep1.outcome, ep1.feedback, ep1.feedback_note)
                inc = await incidents.create(s, title="checkin yavaş", source=IncidentSource.MANUAL,
                                             investigation_id=inv_id)
                await incidents.update_fields(s, inc, root_cause="Hikari pool boyutu düşüktü",
                                              recommended_actions=["maximumPoolSize artır"])
                await incidents.transition(s, inc, IncidentStatus.RESOLVED)
                ep2 = await s.get(AgentEpisode, inv_id, populate_existing=True)
                snap2 = (ep2.incident_id == inc.id, ep2.resolution, ep2.resolved_at is not None)
            # ikinci analiz: farklı kelimelerle aynı tablo → episode benzerlikle gelir, rejected bilgisiyle
            ctx2, em = _ctx(uid, cid, tid)
            await o.turn(ctx2, "Rezervasyon tarafında gecikme var, önceki araştırmalarla karşılaştır")
            human = port.humans[-1]
            async with transaction(rt.sm) as s:
                hist = await episodes.search(s, mechanism="row_lock")
                again = await episodes.backfill(s)
            return snap0, snap1, snap2, human, inv_id, hist, again
        finally:
            await rt.close()
    snap0, snap1, snap2, human, inv_id, hist, again = run(go)
    assert snap0[0] == "unverified" and snap0[2] == "row_lock" and snap0[1] and snap0[3] is None
    assert snap1 == ("rejected", {"correct": 0, "partial": 0, "incorrect": 1}, "asıl sebep pool ayarıydı")
    assert snap2[0] and "Hikari pool boyutu düşüktü" in snap2[1] and "maximumPoolSize" in snap2[1] and snap2[2]
    block = human.split("<history_data>")[1]
    assert '"episodes"' in block and f"inv:{inv_id}" in block and '"outcome": "rejected"' in block
    assert '"similarity"' in block and "Hikari pool" in block and '"current_symptoms"' in block
    assert hist and hist[0]["ref"].startswith("inv:")
    assert again == 0                                                       # tümünün episode'u var: idempotent


def test_healthy_run_creates_no_episode_and_backfill_skips(settings, lg_ready, tmp_path):
    async def go():
        rt, uid, cid, tid = await _setup(settings, tmp_path, gw=gw_factory("normal"))
        try:
            ctx, _ = _ctx(uid, cid, tid)
            res = await ChatOrchestrator(rt).turn(ctx, "Sistemi analiz et")
            async with transaction(rt.sm) as s:
                n = await episodes.backfill(s)
                st = await episodes.stats(s)
            return res, n, st
        finally:
            await rt.close()
    res, n, st = run(go)
    assert res["investigation"]["status"] == "COMPLETED" and n == 0 and st["total"] == 0
    assert _count(settings, "select count(*) from sre.agent_episodes") == 0


def test_backfill_builds_episodes_for_existing_investigations(settings, lg_ready, tmp_path):
    async def go():
        rt, uid, cid, tid = await _setup(settings, tmp_path)
        try:
            ctx, _ = _ctx(uid, cid, tid)
            r = await ChatOrchestrator(rt).turn(ctx, "Checkin neden yavaşladı?")
            inv_id = uuid.UUID(r["investigation"]["investigation_id"])
            async with transaction(rt.sm) as s:
                await s.execute(sa.delete(AgentEpisode))                     # 0004 öncesi durumu taklit et
                n1 = await episodes.backfill(s)
                n2 = await episodes.backfill(s)
                inv = await investigations.get(s, inv_id)
                ep = await s.get(AgentEpisode, inv_id)
                return n1, n2, ep.mechanism, inv.primary_mechanism
        finally:
            await rt.close()
    n1, n2, mech, prim = run(go)
    assert (n1, n2) == (1, 0) and mech == prim == "row_lock"


# ---------------------------------------------------------------- akış: working memory
def _free_ctx(uid, cid, tid):
    """Arayüzden zaman aralığı seçilmemiş tur (odak penceresi devralınabilir)."""
    em = ListEmitter()
    return TurnContext(user_id=uid, conversation_id=cid, thread_id=tid, emit=em), em


def test_focus_carries_app_and_window_across_turns(settings, lg_ready, tmp_path):
    async def go():
        port = FakePort()
        rt, uid, cid, tid = await _setup(settings, tmp_path, llm=port)
        try:
            o = ChatOrchestrator(rt)
            c1, _ = _free_ctx(uid, cid, tid)
            r1 = await o.turn(c1, "Checkin neden yavaşladı?")
            c2, em2 = _free_ctx(uid, cid, tid)
            r2 = await o.turn(c2, "Peki 1 saat öncesi?")
            c3, _ = _free_ctx(uid, cid, tid)
            await o.turn(c3, "Peki bunun sebebi neydi?")
            view = await o.working_memory(tid)
            async with transaction(rt.sm) as s:
                i1 = await investigations.get(s, uuid.UUID(r1["investigation"]["investigation_id"]))
                i2 = await investigations.get(s, uuid.UUID(r2["investigation"]["investigation_id"]))
            cleared = await o.clear_focus(tid)
            after = await o.working_memory(tid)
            return port, em2, i1, i2, view, cleared, after
        finally:
            await rt.close()
    port, em2, i1, i2, view, cleared, after = run(go)
    assert i1.application_id == i2.application_id == "checkin-service"
    assert i2.window_start == i1.window_start - timedelta(hours=1) and i2.window_end == i1.window_end - timedelta(hours=1)
    notes = " ".join(e["data"].get("message", "") for e in em2.events if e["type"] == "agent_thinking")
    assert "Önceki turun odağı kullanıldı" in notes and "checkin-service" in notes
    assert port.focus_hints[0] is None and "checkin-service" in port.focus_hints[1]       # router odağı görür
    follow = port.humans[-1]
    assert "<context_data>" in follow and '"focus"' in follow and '"earlier_turns"' in follow
    assert "Checkin neden yavaşladı?" in follow
    f = view["focus"]
    assert f["apps"] == ["checkin-service"] and f["investigation"] == f"inv:{i2.id}"
    assert f["window"]["start"].startswith(i2.window_start.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M"))
    assert [d["turn"] for d in view["digests"]] == [3, 2, 1] and view["messages_in_state"] == 6
    assert cleared and after["focus"] is None and len(after["digests"]) == 3


def test_digests_outlive_message_window_and_survive_restart(settings, lg_ready, tmp_path):
    turns = MAX_STATE_MESSAGES // 2 + 3

    async def go():
        rt, uid, cid, tid = await _setup(settings, tmp_path)
        o = ChatOrchestrator(rt)
        for i in range(turns):
            ctx, _ = _ctx(uid, cid, tid)
            await o.turn(ctx, f"Daha önce runbook {i} kayıtlı mı?")
        await rt.close()
        from sre_platform.agent.runtime import create_runtime
        from test_agent_memory import _agent_settings
        rt2 = await create_runtime(settings, agent=_agent_settings(tmp_path), gateway_factory=gw_factory())
        try:
            return await ChatOrchestrator(rt2).working_memory(tid)
        finally:
            await rt2.close()
    view = run(go)
    assert view["messages_in_state"] == MAX_STATE_MESSAGES                    # ham mesaj penceresi sınırlı
    assert len(view["digests"]) == turns and view["digests"][-1]["question"] == "Daha önce runbook 0 kayıtlı mı?"
    assert view["digests"][0]["intent"] == "history"


def test_working_memory_api_is_owner_scoped(lg_ready, db_url, monkeypatch, tmp_path):
    from test_api import ADMIN_PW, login, mkuser, with_client
    monkeypatch.setenv("SRE_DATABASE_URL", db_url)
    monkeypatch.setenv("SRE_ADMIN_PASSWORD", ADMIN_PW)
    from sre_platform.core.config import load_platform_settings
    ps = load_platform_settings()

    async def fn(c, app):
        h = await mkuser(c, await login(c), "sahip", "sre")
        other = await mkuser(c, await login(c), "baskasi", "sre")
        me = (await c.get("/api/auth/me", headers=h)).json()
        conv = (await c.post("/api/conversations", headers=h, json={"title": "wm"})).json()
        ctx = TurnContext(user_id=uuid.UUID(me["id"]), conversation_id=uuid.UUID(conv["id"]),
                          thread_id=conv["thread_id"], emit=ListEmitter())
        await ChatOrchestrator(app.state.rt).turn(ctx, "Checkin neden yavaşladı?")
        url = f"/api/conversations/{conv['id']}/memory"
        v = await c.get(url, headers=h)
        assert v.status_code == 200 and v.json()["focus"]["apps"] == ["checkin-service"]
        assert v.json()["digests"][0]["investigation"].startswith("inv:") and v.json()["pending"] == []
        assert (await c.get(url, headers=other)).status_code == 404              # başkasının konuşması
        assert (await c.delete(url + "/focus", headers=other)).status_code == 404
        assert (await c.delete(url + "/focus", headers=h)).status_code == 204
        v2 = (await c.get(url, headers=h)).json()
        assert v2["focus"] is None and len(v2["digests"]) == 1
        m = (await c.get("/api/settings/memory", headers=h)).json()
        assert m["episodes"]["total"] == 1 and m["episodes"]["by_outcome"]["unverified"] == 1
        assert m["working_memory"]["max_messages"] == MAX_STATE_MESSAGES
    with_client(ps, tmp_path, fn)
