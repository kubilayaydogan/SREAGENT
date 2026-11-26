"""Agent Middleware (backend): gerçek InvestigationRunner + ChatOrchestrator + gerçek PostgreSQL; MCP ve Claude sahte.

Doğrulananlar
* Analiz ve sohbet run'ları middleware kaydı üretir: run kökü, before_agent, model/tool zarfları, iç içe wrap_tool_call
  zinciri (parent/child + depth + sequence), tool_call_id = MCP çağrı kaydının kimliği; analiz sonucu değişmez.
* Run Timeline zaman damgasıyla değil sequence + parent ile kurulur; sohbet içindeki analiz iç run olarak bağlanır.
* Yapılandırma: optimistic concurrency (409), geçersiz parametre / sıra çakışması / zorunlu middleware'i kapatma 422,
  audit (başarı + red), sürüm geçmişi; RBAC backend'de (SRE ve viewer yazamaz, viewer okuyamaz, kimliksiz 401).
* Snapshot: run sırasında yapılan değişiklik o run'ı etkilemez; sonraki run yeni sürümü kullanır ve kayıtlar sürümü taşır.
* Engellenen tool sunucuya gönderilmez (MCP tools/call yok, MCP Çağrıları'nda gönderilmedi olarak görünür).
* Sohbet turu kayıtları yalnız sahibine ve admin'e görünür. Dry-run yan etkisizdir (kayıt üretmez).
* Telemetry deposu yazılamazsa agent akışı etkilenmez. Retention eski kayıtları siler.
* ClaudePort streaming: chunk'lar sırasıyla ve buffer'lanmadan iletilir.
"""
from __future__ import annotations

import asyncio
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path[:0] = [str(ROOT / "sre-agent/tests"), str(ROOT / "sre-agent/eval")]

from sqlalchemy import func, select, text, update  # noqa: E402
from test_agent_memory import FakePort, _ctx  # noqa: E402
from test_api import api_settings, lg_ready, login, mkuser, with_client  # noqa: E402,F401
from test_mcp_calls import PLAIN, TOKEN, _run, gateway  # noqa: E402

from airlab_agent import middleware as mw  # noqa: E402
from sre_platform.agent.chat import ChatOrchestrator  # noqa: E402
from sre_platform.db.models import AuditEvent, MiddlewareExecution, Role, ToolExecution  # noqa: E402
from sre_platform.db.session import transaction  # noqa: E402
from sre_platform.repositories import users  # noqa: E402

PLAN = {"http": ["ok"], "db_conn": ["timeout", "ok"]}


async def _rows(rt, **where):
    async with transaction(rt.sm) as s:
        q = select(MiddlewareExecution)
        for k, v in where.items():
            q = q.where(getattr(MiddlewareExecution, k) == v)
        return list((await s.execute(q.order_by(MiddlewareExecution.sequence))).scalars())


def _walk(nodes, depth=0, out=None):
    out = [] if out is None else out
    for n in nodes:
        out.append((depth, n))
        _walk(n["children"], depth + 1, out)
    return out


def test_flow_records_timeline_links_and_visibility(api_settings, tmp_path, lg_ready):
    gw, holder = gateway(PLAN)

    async def fn(c, app):
        h = await login(c)
        rt = app.state.rt
        inv_id, run_id, status, risk = await _run(app)
        async with transaction(rt.sm) as s:
            u = await users.create(s, "mw-owner", "SRE", Role.SRE, "parola-test-12345")
            from sre_platform.repositories import conversations
            conv = await conversations.create(s, u.id, "mw")
            uid, cid, tid = u.id, conv.id, conv.thread_id
            tool_ids = {str(t.id) for t in (await s.execute(select(ToolExecution).where(
                ToolExecution.investigation_id == inv_id, ToolExecution.mcp_method.is_not(None)))).scalars()}
        ctx, _ = _ctx(uid, cid, tid)
        await ChatOrchestrator(rt).turn(ctx, "Checkin neden yavaşladı?")
        chat_run = f"chat-{ctx.result['user_message_id']}"
        rows = await _rows(rt, run_id=run_id)
        ov = (await c.get("/api/middleware/overview", headers=h)).json()
        cat = (await c.get("/api/middleware/catalog", headers=h)).json()
        det = (await c.get("/api/middleware/catalog/tool_param_validation", headers=h)).json()
        tl = (await c.get(f"/api/middleware/runs/{run_id}/timeline", headers=h)).json()
        ex = (await c.get("/api/middleware/executions", headers=h,
                          params={"run_id": run_id, "middleware": "tool_allowlist"})).json()
        one = (await c.get(f"/api/middleware/executions/{ex['items'][0]['execution_id']}", headers=h)).json()
        runs = (await c.get("/api/middleware/runs", headers=h)).json()
        hooks = (await c.get("/api/middleware/hooks", headers=h)).json()
        other = await mkuser(c, h, "mw-other", "sre")
        owner = await login(c, "mw-owner", "parola-test-12345")
        vis = {"owner": (await c.get(f"/api/middleware/runs/{chat_run}/timeline", headers=owner)).status_code,
               "other": (await c.get(f"/api/middleware/runs/{chat_run}/timeline", headers=other)).status_code,
               "admin": (await c.get(f"/api/middleware/runs/{chat_run}/timeline", headers=h)).status_code,
               "other_list": (await c.get("/api/middleware/executions", headers=other,
                                          params={"run_id": chat_run})).json()["total"],
               "other_analysis": (await c.get(f"/api/middleware/runs/{run_id}/timeline", headers=other)).status_code}
        chat_tl = (await c.get(f"/api/middleware/runs/{chat_run}/timeline", headers=owner)).json()
        async with transaction(rt.sm) as s:
            snap = (await s.execute(text("select config_snapshot from sre.investigations where id = :i"),
                                    {"i": inv_id})).scalar_one()
            blob = json.dumps([str(r) for r in (await s.execute(text(
                "select reason, policy, before, after, extra from sre.middleware_executions"))).all()])
        return locals()
    r = with_client(api_settings, tmp_path, fn, gw=gw, llm=FakePort())

    assert str(r["status"]).endswith("COMPLETED") and r["risk"] is not None          # agent akışı değişmedi
    rows = r["rows"]
    root = [x for x in rows if x.entry_type == "agent"]
    assert len(root) == 1 and root[0].sequence == 0 and root[0].outcome == "completed"
    assert {x.hook for x in rows if x.entry_type == "middleware"} >= {"before_agent", "wrap_tool_call", "after_agent"}
    envs = [x for x in rows if x.entry_type == "call" and x.target_kind == "tool"]
    assert envs and {x.tool_call_id for x in envs} == r["tool_ids"]                   # MCP Çağrıları ile ortak kimlik
    assert all(x.parent_execution_id == root[0].id for x in envs)
    # iç içe zincir: allowlist(40) → validation(50) → limit(60) → retry(90); her biri bir öncekinin çocuğu
    by_parent = {x.parent_execution_id: x for x in rows if x.hook == "wrap_tool_call"}
    chain, cur = [], envs[0].id
    while cur in by_parent:
        chain.append(by_parent[cur].middleware)
        cur = by_parent[cur].id
    assert chain == ["call_logging", "timing", "tool_allowlist", "tool_param_validation", "call_limit",
                     "retry_observer"]
    retried = [x for x in rows if x.middleware == "retry_observer" and x.error_code == "retried"]
    assert retried and retried[0].attempt_no == 2                                       # db_conn: timeout → ok
    assert all(x.overhead_ms is not None for x in envs) and all(x.wrapped_ms is not None for x in envs)
    assert r["snap"]["middleware"]["tool_allowlist"]["version"] == 0
    # API
    ov, cat = r["ov"], r["cat"]
    assert ov["registered"] == len(mw.REGISTRY) == len(cat["items"]) and ov["executions"] > 0 and ov["blocked"] == 0
    assert ov["pending_policy_decisions"] is None and ov["overhead_ms"]["p95"] is not None
    assert cat["can_manage"] is True and [i["order"] for i in cat["items"]] == sorted(i["order"] for i in cat["items"])
    assert r["det"]["mandatory"] and r["det"]["params_schema"]["properties"]["max_chars"]
    assert r["det"]["hook_info"]["wrap_tool_call"]["langchain_builtin"] is False
    tl = r["tl"]
    walked = _walk(tl["tree"])
    assert tl["ordering"].startswith("sequence") and walked[0][1]["entry_type"] == "agent"
    seqs = [n["sequence"] for _, n in walked if n["parent_execution_id"] == walked[0][1]["execution_id"]]
    assert seqs == sorted(seqs) and max(d for d, _ in walked) >= 6
    assert r["one"]["links"]["mcp_call"].startswith("/mcp-calls?call=") and r["one"]["cache"] == "not_supported"
    assert r["one"]["before"] is None and r["one"]["payload_captured"] is False        # varsayılan: yalnız metadata
    assert {i["run_id"] for i in r["runs"]["items"]} >= {r["run_id"], r["chat_run"]}
    assert r["hooks"]["unsupported"] and r["hooks"]["framework"].startswith("özel LangGraph")
    # sohbet: route (model) + answer (stream) zarfları; sohbetteki analiz iç run
    chat_tl = r["chat_tl"]
    assert chat_tl["nested_runs"] and chat_tl["root"]["run_kind"] == "chat_turn"
    assert r["vis"] == {"owner": 200, "other": 404, "admin": 200, "other_list": 0, "other_analysis": 200}
    assert TOKEN not in r["blob"] and PLAIN not in r["blob"]


def test_config_update_rbac_concurrency_validation_audit(api_settings, tmp_path):
    async def fn(c, app):
        h = await login(c)
        sre = await mkuser(c, h, "mw-sre", "sre")
        viewer = await mkuser(c, h, "mw-viewer", "viewer")
        out = {"anon": (await c.get("/api/middleware/catalog")).status_code,
               "viewer": (await c.get("/api/middleware/catalog", headers=viewer)).status_code,
               "sre_read": (await c.get("/api/middleware/catalog", headers=sre)).json()["can_manage"],
               "sre_patch": (await c.patch("/api/middleware/catalog/timing", headers=sre,
                                           json={"version": 0, "enabled": False})).status_code}
        p = await c.patch("/api/middleware/catalog/timing", headers=h,
                          json={"version": 0, "params": {"slow_call_ms": 5000}, "note": "test"})
        out["ok"] = p.status_code, p.json()
        out["stale"] = (await c.patch("/api/middleware/catalog/timing", headers=h,
                                      json={"version": 0, "enabled": False})).status_code
        out["bad_param"] = await c.patch("/api/middleware/catalog/timing", headers=h,
                                         json={"version": 1, "params": {"slow_call_ms": 1}})
        out["unknown_param"] = (await c.patch("/api/middleware/catalog/timing", headers=h,
                                              json={"version": 1, "params": {"code": "import os"}})).status_code
        out["mandatory_off"] = await c.patch("/api/middleware/catalog/tool_allowlist", headers=h,
                                             json={"version": 0, "enabled": False})
        out["order_conflict"] = await c.patch("/api/middleware/catalog/timing", headers=h,
                                              json={"version": 1, "order": 40})
        out["unknown_mw"] = (await c.patch("/api/middleware/catalog/exec_python", headers=h,
                                           json={"version": 0})).status_code
        out["disable"] = (await c.patch("/api/middleware/catalog/retry_observer", headers=h,
                                        json={"version": 0, "enabled": False})).json()
        cat = {i["name"]: i for i in (await c.get("/api/middleware/catalog", headers=h)).json()["items"]}
        out["reorder"] = await c.post("/api/middleware/reorder", headers=h, json={"items": [
            {"name": "timing", "order": 25, "version": cat["timing"]["version"]},
            {"name": "call_logging", "order": 15, "version": cat["call_logging"]["version"]}]})
        out["reorder_conflict"] = (await c.post("/api/middleware/reorder", headers=h, json={"items": [
            {"name": "timing", "order": 40, "version": 2}]})).status_code
        out["det"] = (await c.get("/api/middleware/catalog/timing", headers=h)).json()
        async with transaction(app.state.rt.sm) as s:
            out["audit"] = [(a.action, a.target, a.result) for a in (await s.execute(
                select(AuditEvent).where(AuditEvent.section == "middleware").order_by(AuditEvent.id))).scalars()]
        return out
    r = with_client(api_settings, tmp_path, fn)
    assert (r["anon"], r["viewer"], r["sre_read"], r["sre_patch"]) == (401, 403, False, 403)
    assert r["ok"][0] == 200 and r["ok"][1]["version"] == 1 and r["ok"][1]["params"]["slow_call_ms"] == 5000
    assert r["stale"] == 409
    assert r["bad_param"].status_code == 422 and "slow_call_ms" in r["bad_param"].text
    assert r["unknown_param"] == 422
    assert r["mandatory_off"].status_code == 422 and "zorunlu" in r["mandatory_off"].text
    assert r["order_conflict"].status_code == 422 and "tool_allowlist" in r["order_conflict"].text
    assert r["unknown_mw"] == 404 and r["disable"]["enabled"] is False
    assert r["reorder"].status_code == 200 and r["reorder_conflict"] == 422
    det = r["det"]
    assert det["order"] == 25 and det["version"] == 2 and [v["change"] for v in det["versions"]] == ["reorder", "update"]
    assert det["config_json"]["params"]["slow_call_ms"] == 5000 and det["versions"][1]["actor"] == "admin"
    acts = r["audit"]
    assert ("middleware_update", "timing", "ok") in acts and ("middleware_update", "timing", "rejected") in acts
    assert ("middleware_update", "tool_allowlist", "rejected") in acts and ("middleware_reorder", "timing", "ok") in acts


def test_snapshot_fixed_during_run_and_blocked_tool_not_sent(api_settings, tmp_path, lg_ready):
    gw, holder = gateway(PLAN)

    async def fn(c, app):
        h = await login(c)
        rt = app.state.rt
        from sre_platform.middleware import runtime as mw_rt
        from sre_platform.settings import runtime as srt
        rc = await srt.run_config(rt)
        sess = await mw_rt.start(rt, rc, run_id="snap-run", kind="analysis", graph="g")
        await c.patch("/api/middleware/catalog/timing", headers=h, json={"version": 0, "params": {"slow_call_ms": 200}})

        async def model(req):
            return 1
        await mw.run_model_async(mw.Call(kind="model", target="m", task="t"), model)
        await sess.finish("completed")
        during = {x.middleware_version for x in await _rows(rt, run_id="snap-run", middleware="timing")}
        # sonraki run yeni sürümü kullanır; allowlist'ten execute-dql çıkarılınca hiçbir tools/call gönderilmez
        await c.patch("/api/middleware/catalog/tool_allowlist", headers=h, json={
            "version": 0, "params": {"allowed_connectors": ["dynatrace"], "allowed_tools": ["read-only-other"]}})
        inv_id, run_id, status, _ = await _run(app)
        rows = await _rows(rt, run_id=run_id)
        async with transaction(rt.sm) as s:
            sent = (await s.execute(select(func.count()).select_from(ToolExecution).where(
                ToolExecution.investigation_id == inv_id, ToolExecution.mcp_method.is_not(None)))).scalar_one()
        calls = len(holder["s"].calls) if "s" in holder else 0
        return locals()
    r = with_client(api_settings, tmp_path, fn, gw=gw)
    assert r["during"] == {0}
    rows = r["rows"]
    assert {x.middleware_version for x in rows if x.middleware == "timing"} == {1}
    blocks = [x for x in rows if x.middleware == "tool_allowlist" and x.entry_type == "middleware"]
    assert blocks and all(x.decision == "block" and x.error_code == "tool_not_allowed" for x in blocks)
    assert all(x.middleware_version == 1 for x in blocks)
    assert r["calls"] == 0                                             # MCP sunucusuna hiç istek gitmedi


def test_dry_run_api_has_no_side_effects(api_settings, tmp_path):
    async def fn(c, app):
        h = await login(c)
        out = {
            "dql": (await c.post("/api/middleware/dry-run", headers=h, json={
                "name": "tool_param_validation", "hook": "wrap_tool_call",
                "input": {"target": "execute-dql", "dql": "delete bucket x"}})).json(),
            "budget": (await c.post("/api/middleware/dry-run", headers=h, json={
                "name": "call_limit", "hook": "wrap_tool_call", "input": {"target": "execute-dql"},
                "state": {"queries_used": 5, "max_queries": 5}})).json(),
            "redact": (await c.post("/api/middleware/dry-run", headers=h, json={
                "name": "redaction", "hook": "before_model",
                "input": {"messages": [["human", "iletişim: ayse@ornek.com"]]}})).json(),
            "unsupported": (await c.post("/api/middleware/dry-run", headers=h, json={
                "name": "timing", "hook": "wrap_model_call"})).json(),
            "bad_params": (await c.post("/api/middleware/dry-run", headers=h, json={
                "name": "tool_param_validation", "hook": "wrap_tool_call",
                "params": {"allowed_commands": ["delete"]}})).json(),
            "unknown": (await c.post("/api/middleware/dry-run", headers=h, json={
                "name": "nope", "hook": "wrap_tool_call"})).status_code,
            "code": (await c.post("/api/middleware/dry-run", headers=h, json={
                "name": "timing", "hook": "wrap_tool_call", "code": "print(1)"})).status_code,
        }
        out["rows"] = await _rows(app.state.rt)
        return out
    r = with_client(api_settings, tmp_path, fn)
    assert r["dql"]["decision"] == "block" and r["dql"]["code"] == "dql_rejected"
    assert r["budget"]["decision"] == "block"
    assert r["redact"]["decision"] == "modify" and "ayse@ornek.com" not in json.dumps(r["redact"])
    assert r["unsupported"]["supported"] is False and r["bad_params"]["param_errors"]
    assert r["unknown"] == 404 and r["code"] == 422 and r["rows"] == []


def test_telemetry_store_failure_does_not_break_agent(api_settings, tmp_path, monkeypatch, lg_ready):
    gw, _ = gateway(PLAN)
    from sre_platform.middleware import runtime as mw_rt

    def broken(ev):
        raise RuntimeError("kayıt deposu yok")
    monkeypatch.setattr(mw_rt, "_row", broken)

    async def fn(c, app):
        inv_id, run_id, status, risk = await _run(app)
        return status, risk, await _rows(app.state.rt)
    status, risk, rows = with_client(api_settings, tmp_path, fn, gw=gw)
    assert str(status).endswith("COMPLETED") and risk is not None and rows == []


def test_retention_and_settings_section(api_settings, tmp_path):
    async def fn(c, app):
        h = await login(c)
        rt = app.state.rt
        old = datetime.now(timezone.utc) - timedelta(days=40)
        async with transaction(rt.sm) as s:
            import uuid
            for i, ts in enumerate((old, datetime.now(timezone.utc))):
                s.add(MiddlewareExecution(id=uuid.uuid4(), run_id="r", sequence=i, depth=0, entry_type="agent",
                                          started_at=ts, result="success", changed_fields=[], extra={}))
        from sre_platform.worker import retention
        res = await retention(rt, {})
        left = len(await _rows(rt))
        st = (await c.get("/api/middleware/settings", headers=h)).json()
        sec = (await c.get("/api/settings/agent_middleware", headers=h)).status_code
        return res, left, st, sec
    res, left, st, sec = with_client(api_settings, tmp_path, fn)
    assert res["middleware_executions"] == 1 and left == 1
    assert st["record_executions"] is True and st["capture_payloads"] is False and sec == 200


def test_claude_port_stream_preserved_and_wrapped():
    from langchain_core.messages import AIMessageChunk

    from sre_platform.agent.llm_port import ClaudePort

    class Chat:
        def astream(self, msgs, config=None):
            async def gen():
                for t in ("Mer", "ha", " dünya"):
                    yield AIMessageChunk(content=t)
                    await asyncio.sleep(0)
            return gen()

    class Core:
        callbacks, model_name, rca_model_name = [], "m", "m"
        chat = rca_chat = Chat()
        settings = None

        def _track(self, msg):
            pass
    recs: list[dict] = []
    run = mw.RunMw(run_id="s", sink=recs.append)

    async def go():
        mw.RUN.set(run)
        return [c async for c in ClaudePort(Core()).astream("sys", "soru")]
    assert asyncio.run(go()) == ["Mer", "ha", " dünya"]
    env = next(x for x in recs if x["entry_type"] == "call")
    assert env["task"] == "answer" and env["extra"]["stream"]["chunks"] == 3 and env["outcome"] == "completed"
