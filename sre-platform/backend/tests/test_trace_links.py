"""LangSmith trace eşlemesi (platform). MCP ve Claude sahte; PostgreSQL gerçek; LangSmith gönderimi yakalanır
(gerçek langsmith Client + SafeTracer, ağ yerine yakalama — sre-agent/tests/tracecap.py).

1. Manuel ve zamanlanmış investigation tek kök trace'te, doğru trigger / platform run_id / aralık kaynağıyla.
2. MCP çağrı kaydının kimliği = gateway'in mcp_call_id'si = LangSmith span kimliği; çağrı detayından span linki.
4. Checkpoint'ten sürdürülen sohbet turu aynı platform run_id ile yeni girişim (attempt 2) olarak bulunur;
   önceki girişimin eşlemesi korunur.
"""
from __future__ import annotations

import sys
import uuid
from pathlib import Path

import pytest
from sqlalchemy import select

ROOT = Path(__file__).resolve().parents[3]
sys.path[:0] = [str(ROOT / "sre-agent/tests"), str(ROOT / "sre-agent/eval")]

from airlab_agent.config import Settings  # noqa: E402
from airlab_agent.graph import new_run_id  # noqa: E402
from conftest import run  # noqa: E402
from synth import cfg, windows  # noqa: E402
from test_agent_memory import EmittingGateway, FakePort, _ctx, gw_factory  # noqa: E402
from tracecap import Capture  # noqa: E402

from sre_platform.agent.chat import ChatOrchestrator  # noqa: E402
from sre_platform.agent.investigation import InvestigationRunner  # noqa: E402
from sre_platform.agent.runtime import create_runtime  # noqa: E402
from sre_platform.db.models import MessageRole, Role, TraceLink, Trigger  # noqa: E402
from sre_platform.db.session import transaction  # noqa: E402
from sre_platform.repositories import applications, conversations, investigations, users  # noqa: E402
from sre_platform.services import langsmith_trace  # noqa: E402

WS = windows()
PROJECT = "airlab-sre-agent"


@pytest.fixture
def lg_ready(db_url):
    from sre_platform.db import migrate
    migrate.setup_langgraph(db_url.replace("postgresql+psycopg://", "postgresql://"))
    return True


class IdGateway(EmittingGateway):
    """Gerçek gateway gibi mantıksal çağrı başına mcp_call_id ve span bilgisi yayar."""
    issued: list[str] = []

    def run_many(self, items):
        orig = self.on_event
        ids = {k: str(uuid.uuid4()) for k, _ in items}
        IdGateway.issued += list(ids.values())

        def on(ev):
            orig and orig({**ev, "mcp_call_id": ids[ev["key"]], "traced": True})
        self.on_event = on
        try:
            return super().run_many(items)
        finally:
            self.on_event = orig


def id_gw_factory():
    base = gw_factory()

    def make(on_event=None):
        g = base(on_event=on_event)
        return IdGateway(g.results, on_event=on_event)
    return make


async def _noop(*a, **k):
    return None


@pytest.fixture
def cap(monkeypatch):
    c = Capture(PROJECT)
    monkeypatch.setattr("airlab_agent.tracing.setup_tracing", lambda _s: [c.tracer])   # gerçek ağ istemcisi yok
    return c


async def _runtime(settings, tmp_path, cap, *, gw=None, llm=None):
    agent = Settings(data_dir=tmp_path, cfg=cfg(), langsmith_tracing=True, langsmith_project=PROJECT)
    rt = await create_runtime(settings, agent=agent, gateway_factory=gw or id_gw_factory(), llm=llm)
    assert rt.callbacks == [cap.tracer]
    langsmith_trace._templates[PROJECT] = "https://ls.test/r/{run_id}"
    return rt


def test_manual_and_scheduled_single_root_trigger_and_call_ids(settings, lg_ready, tmp_path, cap):

    async def go():
        rt = await _runtime(settings, tmp_path, cap)
        try:
            ids = {}
            for trig, mode in ((Trigger.MANUAL, "manual"), (Trigger.SCHEDULED, "scheduled")):
                async with transaction(rt.sm) as s:
                    await applications.sync_from_config(s, rt.agent.cfg)
                    inv = await investigations.create(s, run_id=new_run_id(mode[:3]), trigger=trig, title=mode,
                                                      window_start=WS[1].start, window_end=WS[1].end)
                    ids[mode] = (inv.id, inv.run_id)
                await InvestigationRunner(rt).run(inv.id, emit=_noop, mode=mode)
            async with transaction(rt.sm) as s:
                links = (await s.execute(select(TraceLink).order_by(TraceLink.created_at))).scalars().all()
                tools = {m: (await investigations.get(s, i, with_children=True)).tools for m, (i, _) in ids.items()}
                info = await langsmith_trace.trace_info(rt, s, ids["manual"][0])
            return ids, links, tools, info
        finally:
            await rt.close()
    ids, links, tools, info = run(go)

    for mode, source in (("manual", "user"), ("scheduled", "scheduler")):
        inv_id, run_id = ids[mode]
        roots = [r for r in cap.roots() if r["id"] == str(inv_id)]
        assert len(roots) == 1 and roots[0]["name"] == f"airlab-{mode}"            # tek kök trace
        m = cap.meta(roots[0])
        assert m["trigger"] == mode and m["platform_run_id"] == run_id and m["environment"] == "lab"
        assert m["attempt"] == 1 and m["thread_id"] == run_id
        plan = next(r for r in cap.children(inv_id) if r["name"] == "plan")
        assert plan["outputs"]["step_summary"]["window_source"] == source
        assert len([r for r in cap.children(inv_id) if r["name"] == "collect"]) == 1   # düğüm iki kez görünmez
    assert [(x.kind, x.trigger, x.attempt) for x in links] == [("analysis", "manual", 1), ("analysis", "scheduled", 1)]
    for x in links:                                    # platform run_id ve LangSmith trace_id ayrı alanlarda
        inv_id, run_id = ids[x.trigger]
        assert x.platform_run_id == run_id and x.langsmith_run_id == inv_id and x.langsmith_trace_id == inv_id
    dql = [t for t in tools["manual"] if t.tool_name == "execute-dql"]
    assert dql and all(str(t.id) in IdGateway.issued and t.tool_arguments["mcp_call_id"] == str(t.id)
                       and t.tool_arguments["langsmith_span"] is True for t in dql)
    inv_id = ids["manual"][0]
    assert info["url"] == f"https://ls.test/r/{inv_id}" and info["call_url_template"] == "https://ls.test/r/{call_id}"
    assert info["platform_run_id"] == ids["manual"][1] and info["links"][0]["langsmith_trace_id"] == str(inv_id)
    fb = {f["key"] for f in cap.feedback if f["run_id"] == str(inv_id)}             # sonuç feedback'i aynı run'a
    assert {"risk_score", "partial_data"} <= fb


def test_resumed_chat_turn_keeps_platform_run_id(settings, lg_ready, tmp_path, cap):

    async def go():
        rt = await _runtime(settings, tmp_path, cap, gw=gw_factory(), llm=FakePort(crash_once=True))
        try:
            async with transaction(rt.sm) as s:
                await applications.sync_from_config(s, rt.agent.cfg)
                u = await users.create(s, "sre-" + uuid.uuid4().hex[:6], "SRE", Role.SRE, "parola-test-12345")
                c = await conversations.create(s, u.id, "test")
                uid, cid, tid = u.id, c.id, c.thread_id
            o = ChatOrchestrator(rt)
            ctx, _ = _ctx(uid, cid, tid)
            with pytest.raises(RuntimeError):
                await o.turn(ctx, "Checkin neden yavaşladı?")
            res = await o.resume(_ctx(uid, cid, tid)[0])
            async with transaction(rt.sm) as s:
                msgs = (await conversations.messages(s, cid)).items
                links = (await s.execute(select(TraceLink).order_by(TraceLink.created_at))).scalars().all()
            return res, msgs, links
        finally:
            await rt.close()
    res, msgs, links = run(go)
    assert res["answer"].startswith("## Özet")
    user_msg = next(m for m in msgs if m.role == MessageRole.USER)
    turns = [x for x in links if x.kind == "chat_turn"]
    assert [(x.platform_run_id, x.attempt, x.resumed) for x in turns] == [
        (str(user_msg.id), 1, False), (str(user_msg.id), 2, True)]                   # aynı platform run_id
    assert turns[0].langsmith_run_id == user_msg.id and turns[1].langsmith_run_id != user_msg.id
    assert all(x.langsmith_trace_id == x.langsmith_run_id for x in turns)            # her girişim kendi kök trace'i
    analysis = [x for x in links if x.kind == "analysis"]
    assert len(analysis) == 1 and analysis[0].langsmith_trace_id == user_msg.id      # analiz ilk turun trace'inde
    roots = sorted((r for r in cap.roots() if r["name"] == "chat-turn"), key=lambda r: cap.meta(r)["attempt"])
    assert [(cap.meta(r)["platform_run_id"], cap.meta(r)["attempt"], cap.meta(r)["resumed"]) for r in roots] == [
        (str(user_msg.id), 1, False), (str(user_msg.id), 2, True)]
    assert "Checkin neden" not in cap.blob()                                         # soru LangSmith'e gitmez
