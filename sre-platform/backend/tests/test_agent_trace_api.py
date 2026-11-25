"""Agent Trace (backend): gerçek InvestigationRunner + ChatOrchestrator, gerçek PostgreSQL; MCP ve Claude sahtedir.

Doğrulananlar
* Analiz run'ı kaydedilir; collect → analyze node çalıştırmalarında Before (State In), Node Update, After (State Out)
  ve Diff API'den açılır; gerçek bir state alanı (`results` → `attribution`) uçtan uca izlenir.
* Sohbet turu ayrı run; içindeki analiz çocuk run olarak bağlanır; LLM yorumlanamadığında fallback işareti route
  node'una bağlanır.
* Kayıt öncesi trace'i olmayan investigation "kaydedilmemiş" (recorded=false) olarak listelenir.
* Canlı takip: SSE id = sequence, Last-Event-ID ile tekrar bağlanınca yinelenen olay yok; bitmiş run'da akış kapanır.
* Maskeleme: MCP token'ı ve gizli değer hiçbir trace olayında yok.
* Yetki: kimliksiz 401, viewer 403; başka kullanıcının sohbet trace'i 404, admin görür.
* Trace deposu yazılamazsa analiz sonucu değişmez; kayıp run satırında sayılır.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path[:0] = [str(ROOT / "sre-agent/tests"), str(ROOT / "sre-agent/eval")]

from sqlalchemy import select, text  # noqa: E402
from test_agent_memory import FakePort, _ctx  # noqa: E402
from test_api import api_settings, lg_ready, login, mkuser, with_client  # noqa: E402,F401
from test_mcp_calls import PLAIN, TOKEN, _run, gateway  # noqa: E402

from sre_platform.agent.chat import ChatOrchestrator  # noqa: E402
from sre_platform.db.models import AgentTraceRun, Role, Trigger  # noqa: E402
from sre_platform.db.session import transaction  # noqa: E402
from sre_platform.repositories import investigations, users  # noqa: E402

PLAN = {"http": ["ok"], "db_conn": ["timeout", "ok"]}


async def _sse(c, url, h, last=None):
    hh = dict(h)
    if last is not None:
        hh["Last-Event-ID"] = str(last)
    out = []
    async with c.stream("GET", url, headers=hh) as r:
        assert r.status_code == 200
        ev, data, eid = None, None, None
        async for line in r.aiter_lines():
            if line.startswith("event:"):
                ev = line.split(":", 1)[1].strip()
            elif line.startswith("data:"):
                data = line.split(":", 1)[1].strip()
            elif line.startswith("id:"):
                eid = line.split(":", 1)[1].strip()
            elif line == "" and ev:
                out.append((ev, eid, json.loads(data) if data else None))
                if ev == "trace_done":
                    break
                ev, data, eid = None, None, None
    return out


def test_analysis_trace_nodes_state_diff_stream_masking(api_settings, tmp_path):
    gw, _ = gateway(PLAN)

    async def fn(c, app):
        h = await login(c)
        inv_id, run_id, *_ = await _run(app)
        async with transaction(app.state.rt.sm) as s:           # kayıt öncesinden kalma (trace'siz) investigation
            cur = await investigations.get(s, inv_id)
            await investigations.create(s, run_id="legacy-run-1", trigger=Trigger.MANUAL, title="eski",
                                        window_start=cur.window_start, window_end=cur.window_end)
        lst = (await c.get("/api/agent-traces", headers=h)).json()
        det = (await c.get(f"/api/agent-traces/{run_id}", headers=h)).json()
        execs = {}
        for e in det["events"]:
            if e["event_type"] == "node_start":
                execs.setdefault(e["node"], e["node_exec_id"])
        nodes = {n: (await c.get(f"/api/agent-traces/{run_id}/node-execs/{x}", headers=h)).json()
                 for n, x in execs.items()}
        ev_full = [(await c.get(f"/api/agent-traces/{run_id}/events/{e['event_id']}", headers=h)).json()
                   for e in det["events"]]
        legacy_det = (await c.get("/api/agent-traces/legacy-run-1", headers=h)).json()
        only_legacy = (await c.get("/api/agent-traces", headers=h, params={"recorded": "false"})).json()
        sse_all = await _sse(c, f"/api/agent-traces/{run_id}/stream", h)
        sse_rest = await _sse(c, f"/api/agent-traces/{run_id}/stream", h, last=5)
        anon = await c.get(f"/api/agent-traces/{run_id}")
        viewer = await mkuser(c, h, "izleyici", "viewer")
        v = await c.get("/api/agent-traces", headers=viewer)
        async with transaction(app.state.rt.sm) as s:
            raw = json.dumps([list(map(str, r)) for r in (await s.execute(text(
                "SELECT payload FROM sre.agent_trace_events"))).all()], ensure_ascii=False)
        return locals()
    r = with_client(api_settings, tmp_path, fn, gw=gw)
    det, nodes = r["det"], r["nodes"]
    run = det["run"]
    assert run["recorded"] and run["status"] == "completed" and run["kind"] == "analysis"
    assert run["thread_id"] == r["run_id"] and run["capture"] in ("complete", "truncated")
    assert run["event_count"] == len(det["events"]) and run["lost_events"] == 0 and run["capture_errors"] == 0
    seqs = [e["sequence"] for e in det["events"]]
    assert seqs == list(range(1, len(seqs) + 1))
    assert det["events"][0]["event_type"] == "run_start" and det["events"][-1]["event_type"] == "run_end"
    # Liste: kayıtlı run + kaydedilmemiş (eski) investigation
    by_run = {x["run_id"]: x for x in r["lst"]["items"]}
    assert by_run[r["run_id"]]["recorded"] is True and by_run["legacy-run-1"]["recorded"] is False
    assert r["legacy_det"]["run"]["recorded"] is False and r["legacy_det"]["events"] == []
    assert {x["run_id"] for x in r["only_legacy"]["items"]} == {"legacy-run-1"}
    # Kabul: collect → analyze; Before / Node Update / After / Diff ve gerçek state alanı
    col, ana = nodes["collect"], nodes["analyze"]
    assert col["before"]["captured"] and col["update"]["captured"] and col["after"]["captured"]
    assert "results" not in (col["before"]["value"] or {}) or not col["before"]["value"]["results"]
    assert set(col["update"]["value"]["results"]) == set(col["after"]["value"]["results"])
    assert any(d["path"].startswith("results.http") and d["change"] == "added" for d in col["diff"]["changes"])
    assert col["transition_out"]["to"] == ["analyze"]
    assert col["transition_out"]["to_exec_ids"] == [ana["node_exec_id"]]
    assert ana["transition_in"]["from"] == ["collect"]
    assert ana["before"]["value"]["results"] == col["after"]["value"]["results"]       # State Out → State In
    assert "attribution" in ana["update"]["value"] and "attribution" in ana["after"]["value"]
    assert any(d["path"].startswith("attribution") for d in ana["diff"]["changes"])
    assert ana["transition_out"]["routing"]["analyze"][0]["function"] == "needs_extra"
    assert ana["step"] == col["step"] + 1 and not ana["after"]["combined"]
    # Canlı takip: tüm olaylar sırayla; Last-Event-ID=5 → yalnız 6.. (yineleme yok); bitince trace_done
    ids = [int(e[1]) for e in r["sse_all"] if e[0] == "trace_event"]
    assert ids == seqs and r["sse_all"][-1][0] == "trace_done"
    assert [int(e[1]) for e in r["sse_rest"] if e[0] == "trace_event"] == seqs[5:]
    # Maskeleme
    for blob in (r["raw"], json.dumps(r["ev_full"], ensure_ascii=False, default=str)):
        assert PLAIN not in blob and TOKEN not in blob and "dqlQueryString" not in blob
    # Yetki
    assert r["anon"].status_code == 401 and r["v"].status_code == 403


def test_chat_turn_trace_child_fallback_and_owner_only(api_settings, tmp_path):
    async def fn(c, app):
        h = await login(c)
        rt = app.state.rt
        async with transaction(rt.sm) as s:
            u = await users.create(s, "sre-trace", "SRE", Role.SRE, "parola-test-12345")
            from sre_platform.repositories import conversations
            conv = await conversations.create(s, u.id, "trace")
            uid, cid, tid = u.id, conv.id, conv.thread_id
        ctx, _ = _ctx(uid, cid, tid)
        res = await ChatOrchestrator(rt).turn(ctx, "Checkin neden yavaşladı?")
        mid = ctx.result["user_message_id"]
        owner = await login(c, "sre-trace", "parola-test-12345")
        other = await mkuser(c, h, "baska-sre", "sre")
        d_owner = (await c.get(f"/api/agent-traces/chat-{mid}", headers=owner)).json()
        d_admin = await c.get(f"/api/agent-traces/chat-{mid}", headers=h)
        d_other = await c.get(f"/api/agent-traces/chat-{mid}", headers=other)
        l_other = (await c.get("/api/agent-traces", headers=other, params={"kind": "chat_turn"})).json()
        child = d_owner["children"][0]
        d_child_other = await c.get(f"/api/agent-traces/{child['run_id']}", headers=other)
        fb = [e for e in d_owner["events"] if e["event_type"] == "fallback"]
        return locals()
    r = with_client(api_settings, tmp_path, fn, llm=FakePort(route_fail=True))
    d = r["d_owner"]
    assert d["run"]["kind"] == "chat_turn" and d["run"]["thread_id"] == str(r["tid"])
    nodes = [e["node"] for e in d["events"] if e["event_type"] == "node_start"]
    assert nodes[:2] == ["route", "investigate"]
    assert r["fb"] and r["fb"][0]["node"] == "route" and r["fb"][0]["node_exec_id"]
    assert r["child"]["kind"] == "analysis" and r["child"]["parent_run_id"] == d["run"]["run_id"]
    assert r["child"]["investigation_id"] == r["res"]["investigation"]["investigation_id"]
    # sohbet trace'i yalnız sahibine ve admin'e; analiz (investigation) ekip geneli
    assert r["d_admin"].status_code == 200 and r["d_other"].status_code == 404
    assert r["l_other"]["total"] == 0 and r["d_child_other"].status_code == 200


def test_trace_store_failure_does_not_change_analysis(api_settings, tmp_path, monkeypatch):
    from sre_platform.services import agent_trace as svc
    gw, _ = gateway(PLAN)

    async def fn(c, app):
        base = await _run(app)

        async def broken_flush(self):
            while not self.q.empty():
                self.q.get_nowait()
                self.lost += 1
        monkeypatch.setattr(svc.TraceSession, "flush", broken_flush)
        again = await _run(app)
        async with transaction(app.state.rt.sm) as s:
            row = (await s.execute(select(AgentTraceRun).where(AgentTraceRun.run_id == again[1]))).scalar_one()
        return base, again, row
    base, again, row = with_client(api_settings, tmp_path, fn, gw=gw)
    assert again[2] == base[2] and again[3] == base[3]                 # durum ve risk aynı
    assert row.lost_events > 0 and row.event_count == 0 and row.status == "completed"
