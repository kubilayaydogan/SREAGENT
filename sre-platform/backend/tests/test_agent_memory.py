"""Faz 3 — ajan hafızası: PostgreSQL checkpointer (konuşma + analiz), uzun vadeli hafıza (Store + FTS),
geçmiş incident/araştırma araması, kesinti/iptal/sürdürme. MCP ve Claude sahte; PostgreSQL gerçek."""
from __future__ import annotations

import sys
import uuid
from pathlib import Path

import pytest
import sqlalchemy as sa

ROOT = Path(__file__).resolve().parents[3]
sys.path[:0] = [str(ROOT / "sre-agent/tests"), str(ROOT / "sre-agent/eval")]

from airlab_agent.config import Settings  # noqa: E402
from airlab_agent.llm import LlmUnavailable  # noqa: E402
from airlab_agent.mcp_gateway import McpUnavailable  # noqa: E402
from conftest import run  # noqa: E402
from scenarios import SCENARIOS  # noqa: E402
from sre_platform.agent.chat import ChatOrchestrator, TurnContext  # noqa: E402
from sre_platform.agent.events import ListEmitter  # noqa: E402
from sre_platform.agent.llm_port import ChatRoute, heuristic_route  # noqa: E402
from sre_platform.agent.runtime import create_runtime  # noqa: E402
from sre_platform.db.models import InvestigationStatus, MessageRole, Role  # noqa: E402
from sre_platform.db.session import transaction  # noqa: E402
from sre_platform.repositories import applications, conversations, incidents, investigations, users  # noqa: E402
from synth import FakeGateway, build, cfg, windows  # noqa: E402

WS = windows()


class EmittingGateway(FakeGateway):
    """Sahte MCP; gerçek gateway gibi sorgu başı/sonu olayı yayar."""

    def __init__(self, *a, on_event=None, **kw):
        super().__init__(*a, **kw)
        self.on_event = on_event

    def run_many(self, items):
        for k, d in items:
            self.on_event and self.on_event({"phase": "start", "key": k, "dql": d, "tool": "execute-dql"})
        try:
            out = super().run_many(items)
        except McpUnavailable as e:
            for k, d in items:
                self.on_event and self.on_event({"phase": "end", "key": k, "dql": d, "status": "UNAVAILABLE",
                                                 "records": 0, "elapsed_ms": 5, "error": str(e)})
            raise
        for k, r in out.items():
            self.on_event and self.on_event({"phase": "end", "key": k, "dql": r.dql, "status": r.status,
                                             "records": len(r.records), "query_id": r.query_id, "elapsed_ms": 7,
                                             "evidence_id": r.evidence_id})
        return out


def gw_factory(scenario="row_lock", down=False):
    base, win, extra = SCENARIOS[scenario]()
    results = build(base, win, WS, **extra)

    def make(on_event=None):
        return EmittingGateway(results, down=down, on_event=on_event)
    return make


class FakePort:
    model_name = "fake-claude"
    rca_model_name = "fake-claude-rca"

    def __init__(self, route_fail=False, stream_fail=False, text="## Özet\nSahte yanıt.", crash_once=False):
        self.route_fail, self.stream_fail, self.text, self.crash_once = route_fail, stream_fail, text, crash_once
        self.humans: list[str] = []
        self.rca_flags: list[bool] = []

    async def route(self, question, history, *, apps, app=None, focus=None):
        self.focus_hints = getattr(self, "focus_hints", []) + [focus]
        if self.route_fail:
            raise LlmUnavailable("simulated")
        return heuristic_route(question, apps, app)

    async def astream(self, system, human, *, rca=False, task="answer"):
        self.humans.append(human)
        self.rca_flags.append(rca)
        if self.crash_once:
            self.crash_once = False
            raise RuntimeError("süreç kesildi (simülasyon)")
        if self.stream_fail:
            raise LlmUnavailable("simulated")
        for part in self.text.split(" "):
            yield part + " "


@pytest.fixture
def lg_ready(db_url):
    from sre_platform.db import migrate
    migrate.setup_langgraph(db_url.replace("postgresql+psycopg://", "postgresql://"))
    return True


def _agent_settings(tmp_path):
    return Settings(data_dir=tmp_path, cfg=cfg())


async def _setup(settings, tmp_path, *, gw=None, llm=None):
    rt = await create_runtime(settings, agent=_agent_settings(tmp_path), gateway_factory=gw or gw_factory(), llm=llm)
    async with transaction(rt.sm) as s:
        await applications.sync_from_config(s, rt.agent.cfg)
        u = await users.create(s, "sre-" + uuid.uuid4().hex[:6], "SRE", Role.SRE, "parola-test-12345")
        c = await conversations.create(s, u.id, "test")
        uid, cid, tid = u.id, c.id, c.thread_id
    return rt, uid, cid, tid


def _ctx(uid, cid, tid, **kw):
    em = ListEmitter()
    return TurnContext(user_id=uid, conversation_id=cid, thread_id=tid, emit=em, start=WS[1].start, end=WS[1].end,
                       **kw), em


# ---------------------------------------------------------------- yönlendirme (DB gerektirmez)
def test_heuristic_route():
    apps = cfg()["apps"]
    r = heuristic_route("Reservation uygulaması neden yavaşladı?", apps)
    assert r.intent == "analyze" and r.focus_apps == ["reservation-service"]
    r = heuristic_route("Geçen hafta oluşan PostgreSQL connection pool sorunları nelerdi?", apps)
    assert r.intent == "history" and r.since_days == 7 and r.mechanism == "pool_saturation"
    r = heuristic_route("Bu incident daha önce yaşandı mı?", apps)
    assert r.intent == "history" and "incidents" in r.history_scope
    assert heuristic_route("Önceki araştırmalarla karşılaştır.", apps).intent == "compare"
    r = heuristic_route("Son 24 saatin hata oranlarını analiz et.", apps)
    assert r.intent == "analyze" and r.minutes == 1440
    assert heuristic_route("Bu problem için kayıtlı bir runbook var mı?", apps).history_scope == ["runbooks"]
    assert isinstance(r, ChatRoute)


# ---------------------------------------------------------------- analiz turu
def test_analyze_turn_persists_everything(settings, lg_ready, tmp_path):
    async def go():
        rt, uid, cid, tid = await _setup(settings, tmp_path)
        try:
            ctx, em = _ctx(uid, cid, tid)
            res = await ChatOrchestrator(rt).turn(ctx, "Checkin uygulaması neden yavaşladı?")
            async with transaction(rt.sm) as s:
                inv = await investigations.get(s, uuid.UUID(res["investigation"]["investigation_id"]), with_children=True)
                msgs = (await conversations.messages(s, cid)).items
                steps = [t.step for t in inv.tools]
            snap = await ChatOrchestrator(rt)._graph(ctx).aget_state({"configurable": {"thread_id": tid}})
            return res, em, inv, msgs, steps, snap
        finally:
            await rt.close()
    res, em, inv, msgs, steps, snap = run(go)
    t = em.types()
    assert t[0] == "agent_thinking" and "investigation_started" in t
    assert t.count("tool_started") >= 15 and t.count("tool_completed") >= 15 and "evidence_found" in t
    assert t.index("investigation_started") < t.index("tool_started") < t.index("memory_recalled") < t.index("token")
    assert t[-3:] == ["suggestions", "message_saved", "investigation_completed"]
    assert inv.status == InvestigationStatus.COMPLETED and inv.primary_mechanism == "row_lock"
    assert inv.explanation and inv.explanation_source.startswith("deterministik")
    assert {"app_metrics", "db_metrics", "logs", "traces", "infra", "correlate", "memory", "rca"} <= set(steps)
    assert all(tl.duration_ms is not None for tl in inv.tools)
    assert [m.role for m in msgs] == [MessageRole.USER, MessageRole.ASSISTANT]
    assert msgs[1].investigation_id == inv.id and msgs[1].meta["intent"] == "analyze"
    assert res["suggestions"] and "Row lock wait" in res["answer"]
    assert [m["role"] for m in snap.values["messages"]] == ["user", "assistant"] and not snap.next
    # çekirdek analiz grafiğinin checkpoint'i de PostgreSQL'de
    eng = sa.create_engine(settings.database_url)
    with eng.connect() as c:
        n = c.execute(sa.text("select count(*) from langgraph.checkpoints where thread_id=:t"), {"t": inv.run_id}).scalar()
    eng.dispose()
    assert n > 0


def test_history_question_uses_memory_and_survives_restart(settings, lg_ready, tmp_path):
    async def go():
        rt, uid, cid, tid = await _setup(settings, tmp_path)
        ctx, _ = _ctx(uid, cid, tid)
        first = await ChatOrchestrator(rt).turn(ctx, "Checkin neden yavaşladı?")
        inv_id = uuid.UUID(first["investigation"]["investigation_id"])
        async with transaction(rt.sm) as s:
            inc = await incidents.create(s, title="checkin kilit beklemesi", source=incidents.IncidentSource.MANUAL,
                                         investigation_id=inv_id, application_id="checkin-service")
            await incidents.update_fields(s, inc, root_cause="reservation uzun transaction ile satırı tuttu",
                                          recommended_actions=["transaction süresini kısalt"])
        await rt.close()
        # --- süreç yeniden başladı: yeni runtime, aynı konuşma
        port = FakePort()
        rt2 = await create_runtime(settings, agent=_agent_settings(tmp_path), gateway_factory=gw_factory(), llm=port)
        try:
            ctx2, em2 = _ctx(uid, cid, tid)
            res = await ChatOrchestrator(rt2).turn(ctx2, "Checkin uygulamasında daha önce benzer kilit sorunu yaşandı mı?")
            snap = await ChatOrchestrator(rt2)._graph(ctx2).aget_state({"configurable": {"thread_id": tid}})
            ctx3, _ = _ctx(uid, cid, tid)
            last = await ChatOrchestrator(rt2).turn(ctx3, "Son araştırmamızda ne tespit etmiştik?")
            return inv_id, res, em2, port, snap, last
        finally:
            await rt2.close()
    inv_id, res, em2, port, snap, last = run(go)
    assert res["intent"] == "history" and res["investigation"] is None
    assert "tool_started" not in em2.types()                       # geçmiş sorusu Dynatrace'e gitmez
    assert port.rca_flags and not any(port.rca_flags)              # geçmiş/takip cevapları RCA modelini kullanmaz
    assert res["answer_source"] == "Claude (fake-claude)"
    human = port.humans[0]
    assert f"inv:{inv_id}" in human and "reservation uzun transaction" in human and "<memory_data>" in human
    assert [m["role"] for m in snap.values["messages"]] == ["user", "assistant", "user", "assistant"]
    assert f"inv:{inv_id}" in port.humans[1]                         # "son araştırmamız" → Store'daki son kayıt


def test_analysis_with_llm_streams_tokens_and_includes_history(settings, lg_ready, tmp_path):
    async def go():
        rt, uid, cid, tid = await _setup(settings, tmp_path, llm=FakePort())
        try:
            o = ChatOrchestrator(rt)
            ctx, _ = _ctx(uid, cid, tid)
            await o.turn(ctx, "Checkin neden yavaşladı?")
            ctx2, em = _ctx(uid, cid, tid)
            res = await o.turn(ctx2, "Checkin neden yavaşladı? Önceki araştırmalarla karşılaştır.")
            return res, em, rt.llm.humans, rt.llm.rca_flags
        finally:
            await rt.close()
    res, em, humans, rca_flags = run(go)
    assert res["intent"] == "compare"
    # Analiz/karşılaştırma cevabı (RCA) RCA modeliyle üretilir
    assert rca_flags and all(rca_flags) and res["answer_source"] == "Claude (fake-claude-rca)"
    assert em.types().count("token") >= 3 and res["answer"].startswith("## Özet")
    assert "<history_data>" in humans[-1] and "row_lock" in humans[-1].split("<history_data>")[1]


def test_mcp_down_is_reported_not_faked(settings, lg_ready, tmp_path):
    async def go():
        rt, uid, cid, tid = await _setup(settings, tmp_path, gw=gw_factory(down=True))
        try:
            ctx, em = _ctx(uid, cid, tid)
            res = await ChatOrchestrator(rt).turn(ctx, "Reservation neden yavaşladı?")
            async with transaction(rt.sm) as s:
                inv = await investigations.get(s, uuid.UUID(res["investigation"]["investigation_id"]), with_children=True)
            return res, em, inv
        finally:
            await rt.close()
    res, em, inv = run(go)
    assert inv.status == InvestigationStatus.PARTIAL and inv.risk_score is None
    assert "tool_failed" in em.types() and all(t.execution_status.value != "SUCCEEDED"
                                               for t in inv.tools if t.tool_name == "execute-dql")
    assert res["answer"].startswith("> **Uyarı:**") and "MCP erişilemedi" in res["answer"]


def test_llm_outage_falls_back_to_deterministic(settings, lg_ready, tmp_path):
    async def go():
        rt, uid, cid, tid = await _setup(settings, tmp_path, llm=FakePort(route_fail=True, stream_fail=True))
        try:
            ctx, em = _ctx(uid, cid, tid)
            return await ChatOrchestrator(rt).turn(ctx, "Checkin neden yavaşladı?"), em
        finally:
            await rt.close()
    res, em = run(go)
    msgs = " ".join(e["data"].get("message", "") for e in em.events if e["type"] == "agent_thinking")
    assert "kural tabanlı" in msgs and "deterministik" in msgs and "Row lock wait" in res["answer"]


def test_cancel_marks_investigation_cancelled(settings, lg_ready, tmp_path):
    async def go():
        rt, uid, cid, tid = await _setup(settings, tmp_path)
        try:
            ctx, em = _ctx(uid, cid, tid)
            ctx.cancel.set()
            res = await ChatOrchestrator(rt).turn(ctx, "Checkin neden yavaşladı?")
            async with transaction(rt.sm) as s:
                inv = await investigations.get(s, uuid.UUID(res["investigation"]["investigation_id"]))
            return em, inv
        finally:
            await rt.close()
    em, inv = run(go)
    assert inv.status == InvestigationStatus.CANCELLED
    assert any(e["type"] == "error" and e["data"]["code"] == "cancelled" for e in em.events)


def test_interrupted_turn_resumes_from_checkpoint(settings, lg_ready, tmp_path):
    async def go():
        port = FakePort(crash_once=True)
        rt, uid, cid, tid = await _setup(settings, tmp_path, llm=port)
        try:
            o = ChatOrchestrator(rt)
            ctx, _ = _ctx(uid, cid, tid)
            with pytest.raises(RuntimeError):
                await o.turn(ctx, "Checkin neden yavaşladı?")
            pending = await o.pending(tid)
            async with transaction(rt.sm) as s:
                before = (await investigations.list_(s)).total
            ctx2, em = _ctx(uid, cid, tid)
            res = await o.resume(ctx2)
            async with transaction(rt.sm) as s:
                after = (await investigations.list_(s)).total
                msgs = (await conversations.messages(s, cid)).items
            return pending, res, before, after, msgs, em
        finally:
            await rt.close()
    pending, res, before, after, msgs, em = run(go)
    assert pending == ["respond"]                     # analiz tamamlanmıştı; yalnız yanıt adımı kaldı
    assert before == after == 1                       # sürdürmede analiz TEKRARLANMADI
    assert "tool_started" not in em.types() and res["answer"].startswith("## Özet")
    assert [m.role for m in msgs] == [MessageRole.USER, MessageRole.ASSISTANT]
