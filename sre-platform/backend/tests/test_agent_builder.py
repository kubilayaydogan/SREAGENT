"""Agent Builder: oluştur → model seç → workflow kaydet → test et → yayımla → Soru-Cevap ve schedule'dan çalıştır.

Gerçek PostgreSQL, gerçek FastAPI uçları, gerçek LangGraph derlemesi, gerçek TracedChatAnthropic / Anthropic SDK.
Ağ yerine httpx MockTransport: sağlayıcı yanıtı test düzeneğidir ve isteğin `model` alanını geri yazar; böylece
run kaydındaki modelin sağlayıcıya GERÇEKTEN giden model olduğu doğrulanır. MCP için sahte gateway (yalnız
`resolve_gateway` yerine; policy / allowlist kontrolleri gerçek).

Kabul kriterleri: gömülü agent uyumu (1), uçtan uca özel agent (2), iki agent iki model (3), Soru-Cevap A ile
eşzamanlı schedule B (4), taslak canlıyı etkilemez / başlamış run kendi sürümü (5), geçersiz workflow / yetkisiz tool /
aktif olmayan agent nedeniyle (6); import / export güvenliği ve RBAC.
"""
from __future__ import annotations

import asyncio
import copy
import json
import uuid


from sqlalchemy import func, select  # noqa: E402
from test_api import api_settings, lg_ready, login, mkuser, sse, with_client  # noqa: E402,F401

from airlab_agent.mcp_gateway import QueryResult  # noqa: E402
from sre_platform.builder import engine, runs  # noqa: E402
from sre_platform.db.models import (SYSTEM_AGENT_ID, Agent, AgentRun, AgentTraceRun, Connector,  # noqa: E402
                                    Conversation, LlmProfile)
from sre_platform.db.session import transaction  # noqa: E402

try:
    import httpx2 as hx
except ImportError:  # pragma: no cover
    import httpx as hx

OPUS, SONNET = "claude-opus-5-5", "claude-sonnet-5-5"


# ------------------------------------------------------------------------------------------------ sağlayıcı düzeneği
def _sse(events: list[dict]) -> bytes:
    return "".join(f"event: {e['type']}\ndata: {json.dumps(e)}\n\n" for e in events).encode()


def _handler(sent: list[dict], delay: float = 0.0):
    def h(request):
        body = json.loads(request.content)
        sent.append(body)
        model = body["model"]
        fmt = (body.get("output_config") or {}).get("format")
        text = json.dumps({"severity": "high", "summary": "ok"}) if fmt else f"yanıt [{model}]"
        if body.get("stream"):
            return hx.Response(200, headers={"content-type": "text/event-stream"}, content=_sse([
                {"type": "message_start", "message": {"id": "m1", "type": "message", "role": "assistant", "model": model,
                                                      "content": [], "stop_reason": None, "stop_sequence": None,
                                                      "usage": {"input_tokens": 300, "output_tokens": 1}}},
                {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}},
                {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": text}},
                {"type": "content_block_stop", "index": 0},
                {"type": "message_delta", "delta": {"stop_reason": "end_turn", "stop_sequence": None},
                 "usage": {"input_tokens": 300, "output_tokens": 12}},
                {"type": "message_stop"}]))
        return hx.Response(200, json={"id": "m2", "type": "message", "role": "assistant", "model": model,
                                      "content": [{"type": "text", "text": text}], "stop_reason": "end_turn",
                                      "stop_sequence": None, "usage": {"input_tokens": 200, "output_tokens": 10}})
    return h


def _mock_llm(monkeypatch, sent):
    orig = engine.make_client

    def mk(**kw):
        c = orig(**kw)
        for cl in (c._async_client._client, c._client._client):
            cl._transport, cl._mounts = hx.MockTransport(_handler(sent)), {}
        return c
    monkeypatch.setattr(engine, "make_client", mk)
    monkeypatch.setenv("SRE_SECRET_AB_KEY", "test-only-anthropic-key")
    engine._CLIENTS.clear()


class FakeGw:
    def __init__(self, calls):
        self.calls = calls

    def run_many(self, items):
        self.calls.extend(items)
        return {k: QueryResult(k, d, "OK", records=[{"p95_ms": 1800}]) for k, d in items}


async def _fake_gateway(s, d, env):
    calls: list = []
    return (lambda timeout: FakeGw(calls)), {"connector": "dynatrace"}


async def _seed(rt) -> dict:
    async with transaction(rt.sm) as s:
        ids = {}
        for name, model in (("ab-opus", OPUS), ("ab-sonnet", SONNET)):
            p = LlmProfile(name=name, provider="anthropic", model=model, secret_ref="env:SRE_SECRET_AB_KEY",
                           max_tokens=4096, timeout_s=30, retries=0, enabled=True,
                           last_check={"ok": True, "model": model, "model_max_tokens": 64000,
                                       "capabilities": {"structured_outputs": True, "effort": True}})
            s.add(p)
            await s.flush()
            ids[name] = str(p.id)
        con = (await s.execute(select(Connector).where(Connector.name == "dynatrace"))).scalar_one_or_none()
        if con is None:
            con = Connector(name="dynatrace", provider="dynatrace", transport="streamable_http",
                            endpoint="https://mcp.example.test/mcp", auth_type="bearer", secret_ref="env:SRE_SECRET_AB_DT",
                            enabled=True, read_only=True, allowed_tools=["execute-dql"],
                            capabilities={"tools": [{"name": "execute-dql", "hash": "h-dql", "read_only": True},
                                                    {"name": "create-notebook", "hash": "h-nb", "read_only": False,
                                                     "destructive": True}]})
            s.add(con)
            await s.flush()
        ids["connector"] = str(con.id)
        return ids


def _defn(profile: str, *, nodes=None, edges=None, **settings) -> dict:
    d = {"format": 1,
         "settings": {"model": {"llm_profile_id": profile, "max_tokens": 2048},
                      "instructions": {"system_prompt": "Kısa yanıt ver.", "task_instructions": "", "variables": []},
                      "limits": {"timeout_s": 60, "max_steps": 20, "max_tool_calls": 5, "max_llm_calls": 5,
                                 "token_budget": 100000, "retries": 0}},
         "workflow": {"nodes": nodes or [
             {"id": "start", "type": "start", "label": "Başlangıç", "position": {"x": 10, "y": 20}},
             {"id": "llm_1", "type": "llm", "label": "Yanıt", "position": {"x": 250, "y": 20},
              "config": {"prompt": "Soru: {{input}}", "output_var": "answer", "stream": True}},
             {"id": "result", "type": "result", "label": "Sonuç", "position": {"x": 500, "y": 20},
              "config": {"template": "{{answer}}"}}],
             "edges": edges or [{"id": "e1", "source": "start", "target": "llm_1"},
                                {"id": "e2", "source": "llm_1", "target": "result"}]},
         "layout": {"viewport": {"x": 12.5, "y": -4, "zoom": 0.8}}}
    for k, v in settings.items():
        d["settings"][k] = {**d["settings"].get(k, {}), **v}
    return d


async def _agent(c, h, name, modes, defn, *, publish_h=None) -> dict:
    r = await c.post("/api/agents", headers=h, json={"name": name, "modes": modes})
    assert r.status_code == 201, r.text
    a = r.json()
    r = await c.put(f"/api/agents/{a['id']}/draft", headers=h,
                    json={"revision": a["draft_revision"], "name": name, "modes": modes, "definition": defn})
    assert r.status_code == 200, r.text
    a["draft_revision"] = r.json()["draft_revision"]
    if publish_h is not None:
        r = await c.post(f"/api/agents/{a['id']}/publish", headers=publish_h, json={"revision": a["draft_revision"]})
        assert r.status_code == 200, r.text
    return a


async def _chat(c, h, message, **kw) -> tuple[dict, list[dict]]:
    r = await c.post("/api/agent/chat", headers=h, json={"message": message, **kw})
    assert r.status_code == 202, r.text
    out = r.json()
    ev = sse((await c.get(out["stream_url"], headers=h)).text)
    return out, ev


# ------------------------------------------------------------------------------------------------ uçtan uca
def test_custom_agents_end_to_end(api_settings, tmp_path, lg_ready, monkeypatch):
    sent: list[dict] = []
    _mock_llm(monkeypatch, sent)

    async def fn(c, app):
        rt = app.state.rt
        h = await login(c)
        ids = await _seed(rt)
        sre = await mkuser(c, h, "ab-sre", "sre")
        # Agent A: soru-cevap + zamanlanmış, Opus; SRE oluşturur / test eder, admin yayımlar
        a = await _agent(c, sre, "Agent A", ["chat", "scheduled"], _defn(ids["ab-opus"]))
        assert (await c.post(f"/api/agents/{a['id']}/publish", headers=sre,
                             json={"revision": a["draft_revision"]})).status_code == 403
        # taslak testi (SSE): sonuç, süre, çalışan node'lar, token kullanımı
        r = await c.post(f"/api/agents/{a['id']}/test", headers=sre, json={"input": "merhaba", "semantics": "chat"})
        assert r.status_code == 202, r.text
        tev = sse((await c.get(r.json()["stream_url"], headers=sre)).text)
        res = next(e["data"] for e in tev if e["type"] == "agent_result")
        assert res["status"] == "completed" and res["output"] == f"yanıt [{OPUS}]"
        assert [n["node_id"] for n in res["nodes"]] == ["start", "llm_1", "result"]
        assert res["usage"]["input_tokens"] == 300 and res["duration_ms"] >= 0 and res["version_label"] == "draft"
        assert (await c.post(f"/api/agents/{a['id']}/publish", headers=h,
                             json={"revision": a["draft_revision"], "note": "ilk"})).json()["version"] == 1
        # Agent B: yalnız zamanlanmış, Sonnet; Koşul → iki dal
        b_nodes = [
            {"id": "start", "type": "start", "label": "Başlangıç"},
            {"id": "cond", "type": "condition", "label": "Acil mi?",
             "config": {"field": "input", "operator": "contains", "value": "acil"}},
            {"id": "llm_1", "type": "llm", "label": "Özet", "config": {"prompt": "{{input}}", "output_var": "ozet"}},
            {"id": "r_acil", "type": "result", "label": "Acil", "config": {"template": "ACİL: {{ozet}}"}},
            {"id": "r_normal", "type": "result", "label": "Normal", "config": {"template": "normal: {{input}}"}}]
        b_edges = [{"id": "e1", "source": "start", "target": "cond"},
                   {"id": "e2", "source": "cond", "target": "llm_1", "branch": "true"},
                   {"id": "e3", "source": "cond", "target": "r_normal", "branch": "false"},
                   {"id": "e4", "source": "llm_1", "target": "r_acil"}]
        b = await _agent(c, sre, "Agent B", ["scheduled"], _defn(ids["ab-sonnet"], nodes=b_nodes, edges=b_edges),
                         publish_h=h)
        # B soru-cevap modunu desteklemez → seçicide yok, doğrudan istek 409
        chat_list = (await c.get("/api/agents?mode=chat&runnable=true", headers=sre)).json()["items"]
        assert {x["name"] for x in chat_list} == {"SRE Agent", "Agent A"}
        r = await c.post("/api/agent/chat", headers=sre, json={"message": "x", "agent_id": b["id"]})
        assert r.status_code == 409 and r.json()["error"] == "mode_unsupported"
        # schedule B (Settings › Scheduling) — Soru-Cevap A ile eşzamanlı
        r = await c.post("/api/agents/schedules", headers=h, json={"name": "b-gece", "agent_id": b["id"],
                                                                     "interval_minutes": 60, "input": "acil durum"})
        assert r.status_code == 201, r.text
        from sre_platform import worker
        job = {"agent_id": b["id"], "input": "acil durum", "created_by": "admin", "_job_name": "agent:b-gece"}
        (out_a, ev_a), out_b = await asyncio.gather(_chat(c, sre, "Agent A, durum?", agent_id=a["id"]),
                                                    worker.agent_run(rt, job))
        assert any(e["type"] == "message_saved" for e in ev_a), ev_a
        assert "".join(e["data"]["text"] for e in ev_a if e["type"] == "token") == f"yanıt [{OPUS}]"     # streaming
        assert out_b["status"] == "completed" and out_b["models"] == [SONNET]
        async with transaction(rt.sm) as s:
            conv = await s.get(Conversation, uuid.UUID(out_a["conversation_id"]))
            assert str(conv.agent_id) == a["id"]
            rows = {r.run_id: r for r in (await s.execute(select(AgentRun))).scalars()}
        ra = next(r for r in rows.values() if r.mode == "chat" and str(r.agent_id) == a["id"])
        rb = rows[out_b["agent_run_id"]]
        assert (ra.version_label, [m["model"] for m in ra.models]) == ("v1", [OPUS])
        assert (rb.version_label, rb.schedule_name, [m["model"] for m in rb.models]) == ("v1", "agent:b-gece", [SONNET])
        assert [n["node_id"] for n in rb.nodes] == ["start", "cond", "llm_1", "r_acil"]
        assert rb.output == f"ACİL: yanıt [{SONNET}]"
        # sağlayıcıya giden gerçek modeller ve API anahtarı hiçbir kayda yazılmadı
        assert {x["model"] for x in sent} >= {OPUS, SONNET}
        assert "test-only-anthropic-key" not in json.dumps([ra.models, ra.nodes, rb.models, rb.nodes, rb.output])
        tr = (await c.get(f"/api/agent-traces?agent_id={b['id']}&agent_mode=scheduled", headers=h)).json()
        assert [i["run_id"] for i in tr["items"]] == [rb.run_id] and tr["items"][0]["agent_version"] == "v1"
        llm_ev = [e for e in (await c.get(f"/api/agent-traces/{rb.run_id}", headers=h)).json()["events"]
                  if e["event_type"] == "llm_request"]
        assert llm_ev and llm_ev[0]["node"] == "llm_1" and llm_ev[0]["summary"]["model"] == SONNET
        # aynı konuşmada agent değiştirilemez
        r = await c.post("/api/agent/chat", headers=sre, json={"message": "x", "agent_id": str(SYSTEM_AGENT_ID),
                                                               "conversation_id": out_a["conversation_id"]})
        assert r.status_code == 409
        # (5) taslakta model değiştirmek canlıyı etkilemez; yayın sonrası sonraki run yeni sürüm
        det = (await c.get(f"/api/agents/{a['id']}", headers=sre)).json()
        new = _defn(ids["ab-sonnet"])
        new["settings"]["instructions"]["system_prompt"] = "Yeni talimat."
        r = await c.put(f"/api/agents/{a['id']}/draft", headers=sre, json={
            "revision": det["draft_revision"], "name": "Agent A", "modes": ["chat", "scheduled"], "definition": new})
        rev = r.json()["draft_revision"]
        n0 = len(sent)
        await _chat(c, sre, "taslak etkisi?", conversation_id=out_a["conversation_id"])
        assert sent[n0]["model"] == OPUS and "Yeni talimat." not in json.dumps(sent[n0])
        async with transaction(rt.sm) as s:                       # başlamış run: sürüm koşu başında çözüldü
            ag = await s.get(Agent, uuid.UUID(a["id"]))
            from sre_platform.builder import service
            ag, ver = await service.resolve(s, ag.id, "scheduled")
        spec = runs._spec_from(ag, ver, mode="scheduled", semantics="scheduled", input="x", trigger="schedule")
        assert (await c.post(f"/api/agents/{a['id']}/publish", headers=h, json={"revision": rev})).json()["version"] == 2
        pinned = await engine.execute(rt, spec)
        assert pinned["version_label"] == "v1" and [m["model"] for m in pinned["models"]] == [OPUS]
        n1 = len(sent)
        await _chat(c, sre, "yeni sürüm?", conversation_id=out_a["conversation_id"])
        assert sent[n1]["model"] == SONNET and "Yeni talimat." in json.dumps(sent[n1])
        # önceki sürüm incelenip tekrar aktif edilebilir (audit'li)
        v1 = (await c.get(f"/api/agents/{a['id']}/versions/1", headers=sre)).json()
        assert v1["definition"]["settings"]["model"]["llm_profile_id"] == ids["ab-opus"]
        assert (await c.post(f"/api/agents/{a['id']}/versions/1/activate", headers=h)).json()["active_version"] == 1
        # varsayılan agent + arşiv bağımlılığı
        assert (await c.put("/api/agents/default", headers=sre, json={"agent_id": a["id"]})).status_code == 403
        assert (await c.put("/api/agents/default", headers=h, json={"agent_id": a["id"]})).status_code == 200
        out_d, _ = await _chat(c, sre, "varsayılan?")                       # agent_id yok → platform varsayılanı
        async with transaction(rt.sm) as s:
            assert str((await s.get(Conversation, uuid.UUID(out_d["conversation_id"]))).agent_id) == a["id"]
        r = await c.post(f"/api/agents/{a['id']}/archive", headers=h)
        assert r.status_code == 409 and "Soru-Cevap varsayılan" in r.json()["message"]
        r = await c.post(f"/api/agents/{b['id']}/archive", headers=h)
        assert r.status_code == 409 and "agent:b-gece" in r.json()["message"]
        await c.put("/api/agents/default", headers=h, json={"agent_id": str(SYSTEM_AGENT_ID)})
        await c.delete("/api/agents/schedules/agent:b-gece", headers=h)
        assert (await c.post(f"/api/agents/{a['id']}/archive", headers=h)).status_code == 200
        # (6) arşivlenmiş agent: Soru-Cevap 409, schedule anlaşılır hata + rejected kaydı (başka agent'a geçmez)
        r = await c.post("/api/agent/chat", headers=sre, json={"message": "x", "agent_id": a["id"]})
        assert r.status_code == 409 and r.json()["error"] == "agent_archived"
        try:
            await worker.agent_run(rt, {"agent_id": a["id"], "input": "x", "created_by": "admin", "_job_name": "agent:z"})
            raise AssertionError("arşivli agent çalıştı")
        except RuntimeError as e:
            assert "agent_archived" in str(e)
        async with transaction(rt.sm) as s:
            rej = (await s.execute(select(AgentRun).where(AgentRun.schedule_name == "agent:z"))).scalar_one()
            assert (rej.status, rej.error_code, str(rej.agent_id)) == ("rejected", "agent_archived", a["id"])
            audit = (await s.execute(select(func.count()).select_from(__import__("sre_platform.db.models", fromlist=["AuditEvent"]).AuditEvent)
                                     .where(__import__("sre_platform.db.models", fromlist=["AuditEvent"]).AuditEvent.action.in_(
                                         ["agent.publish", "agent.default.change", "agent.version.activate"])))).scalar()
            assert audit >= 5
        # liste: sistem agent'ı, yayımlanmış sürüm, model, modlar, son çalıştırma
        items = {x["name"]: x for x in (await c.get("/api/agents?archived=true", headers=sre)).json()["items"]}
        assert items["SRE Agent"]["kind"] == "system" and items["SRE Agent"]["can_copy"] is False
        assert items["Agent B"]["published_version_label"] == "v1" and items["Agent B"]["model"]["model"] == SONNET
        assert items["Agent B"]["last_run"]["status"] == "completed"
        return True

    assert with_client(api_settings, tmp_path, fn)


def test_embedded_agent_still_default(api_settings, tmp_path, lg_ready):
    """(1) Gömülü agent: agent seçilmeden soru-cevap mevcut akışla çalışır; run kaydı builtin."""
    async def fn(c, app):
        h = await login(c)
        out, ev = await _chat(c, h, "Son 30 dakikayı analiz et.")
        assert any(e["type"] == "message_saved" for e in ev)
        async with transaction(app.state.rt.sm) as s:
            conv = await s.get(Conversation, uuid.UUID(out["conversation_id"]))
            row = (await s.execute(select(AgentRun).where(AgentRun.conversation_id == conv.id))).scalar_one()
        assert conv.agent_id is None and (row.agent_id, row.version_label, row.status) == (SYSTEM_AGENT_ID, "builtin",
                                                                                          "completed")
        lst = (await c.get("/api/agents?mode=chat&runnable=true", headers=h)).json()
        assert lst["default_chat_agent_id"] == str(SYSTEM_AGENT_ID)
        r = await c.put(f"/api/agents/{SYSTEM_AGENT_ID}/draft", headers=h,
                        json={"revision": 1, "name": "x", "modes": ["chat"], "definition": {}})
        assert r.status_code == 422
        assert (await c.get(f"/api/agents/{SYSTEM_AGENT_ID}/export", headers=h)).status_code == 422
        return True
    assert with_client(api_settings, tmp_path, fn)


# ------------------------------------------------------------------------------------------------ doğrulama
def test_validation_errors_on_nodes(api_settings, tmp_path, lg_ready, monkeypatch):
    async def fn(c, app):
        h = await login(c)
        ids = await _seed(app.state.rt)
        a = await _agent(c, h, "Doğrulama", ["chat"], _defn(ids["ab-opus"]))
        bad = _defn(ids["ab-opus"], nodes=[
            {"id": "start", "type": "start"},
            {"id": "llm_1", "type": "llm", "config": {"prompt": "{{yok}}", "output_var": "a", "temperature": 0.2}},
            {"id": "llm_2", "type": "llm", "config": {"prompt": "x", "output_var": "b"}},
            {"id": "tool", "type": "mcp_tool", "config": {"tool": "create-notebook", "args": {}, "output_var": "t"}},
            {"id": "result", "type": "result", "config": {"template": "{{a}}"}}],
            edges=[{"id": "e1", "source": "start", "target": "llm_1"}, {"id": "e2", "source": "llm_1", "target": "llm_2"},
                   {"id": "e3", "source": "llm_2", "target": "llm_1"}, {"id": "e4", "source": "llm_2", "target": "tool"},
                   {"id": "e5", "source": "tool", "target": "result"}],
            tools={"connector_id": ids["connector"], "allowed_tools": ["create-notebook"]})
        r = await c.put(f"/api/agents/{a['id']}/draft", headers=h, json={
            "revision": a["draft_revision"], "name": "Doğrulama", "modes": ["chat"], "definition": bad})
        assert r.status_code == 422 and r.json()["error"] == "agent_invalid"
        codes = {(i["code"], i["node_id"]) for i in r.json()["details"]}
        assert ("cycle", "llm_1") in codes and ("cycle", "llm_2") in codes
        assert ("single_outgoing", "llm_2") in codes
        # yapısal olarak geçerli ama bağımlılık hatalı: taslak kaydedilir (uyarı), yayın engellenir
        dep = _defn(ids["ab-opus"], nodes=[
            {"id": "start", "type": "start"},
            {"id": "llm_1", "type": "llm", "config": {"prompt": "{{input}}", "output_var": "a", "temperature": 0.2}},
            {"id": "tool", "type": "mcp_tool", "config": {"tool": "create-notebook", "args": {}, "output_var": "t"}},
            {"id": "cond", "type": "condition", "config": {"field": "t.status", "operator": "eq"}},
            {"id": "r1", "type": "result", "config": {"template": "{{a}}"}},
            {"id": "r2", "type": "result", "config": {"template": "{{bilinmeyen}}"}}],
            edges=[{"id": "e1", "source": "start", "target": "llm_1"}, {"id": "e2", "source": "llm_1", "target": "tool"},
                   {"id": "e3", "source": "tool", "target": "cond"},
                   {"id": "e4", "source": "cond", "target": "r1", "branch": "true"},
                   {"id": "e5", "source": "cond", "target": "r2", "branch": "false"}],
            tools={"connector_id": ids["connector"], "allowed_tools": ["create-notebook"]})
        r = await c.put(f"/api/agents/{a['id']}/draft", headers=h, json={
            "revision": a["draft_revision"], "name": "Doğrulama", "modes": ["chat"], "definition": dep})
        # bilinmeyen değişken ve eksik koşul değeri yapısal hatadır
        assert r.status_code == 422
        codes = {(i["code"], i["node_id"], i["field"]) for i in r.json()["details"]}
        assert ("unknown_variable", "r2", "template") in codes and ("config_missing", "cond", "value") in codes
        dep["workflow"]["nodes"][3]["config"]["value"] = "OK"
        dep["workflow"]["nodes"][5]["config"]["template"] = "hata: {{t.error}}"
        r = await c.put(f"/api/agents/{a['id']}/draft", headers=h, json={
            "revision": a["draft_revision"], "name": "Doğrulama", "modes": ["chat"], "definition": dep})
        assert r.status_code == 200, r.text
        warn = {(i["code"], i["node_id"]) for i in r.json()["issues"]}
        assert ("temperature_unsupported", "llm_1") in warn
        assert any(c_.startswith("tool_") and n == "tool" for c_, n in warn)          # yetkisiz tool
        r = await c.post(f"/api/agents/{a['id']}/publish", headers=h, json={"revision": r.json()["draft_revision"]})
        assert r.status_code == 422
        errs = {(i["node_id"], i["code"]) for i in r.json()["details"] if i["node_id"]}
        assert ("llm_1", "temperature_unsupported") in errs
        assert any(n == "tool" and c_.startswith("tool_") for n, c_ in errs)         # yetkisiz tool node'da
        r = await c.post(f"/api/agents/{a['id']}/test", headers=h, json={"input": "x"})
        assert r.status_code == 422                                                  # test de aynı doğrulama
        # yayımlanmamış agent ile soru-cevap: neden gösterilir
        r = await c.post("/api/agent/chat", headers=h, json={"message": "x", "agent_id": a["id"]})
        assert r.status_code == 409 and r.json()["error"] == "agent_unpublished" and "yayımlanmamış" in r.json()["message"]
        return True
    assert with_client(api_settings, tmp_path, fn)


def test_mcp_tool_and_structured_output(api_settings, tmp_path, lg_ready, monkeypatch):
    sent: list[dict] = []
    _mock_llm(monkeypatch, sent)
    monkeypatch.setattr(engine, "resolve_gateway", _fake_gateway)

    async def fn(c, app):
        h = await login(c)
        rt = app.state.rt
        ids = await _seed(rt)
        nodes = [{"id": "start", "type": "start"},
                 {"id": "q", "type": "mcp_tool", "config": {"tool": "execute-dql", "output_var": "p95",
                                                            "args": {"dqlQueryString": "fetch spans | limit 5"}}},
                 {"id": "cond", "type": "condition", "config": {"field": "p95.status", "operator": "eq", "value": "OK"}},
                 {"id": "so", "type": "structured_output",
                  "config": {"prompt": "Veri: {{p95}}", "output_var": "risk", "llm_profile_id": ids["ab-sonnet"],
                             "schema": {"type": "object", "properties": {"severity": {"type": "string"},
                                                                         "summary": {"type": "string"}},
                                        "required": ["severity"], "additionalProperties": False}}},
                 {"id": "r_ok", "type": "result", "config": {"template": "{{risk}}"}},
                 {"id": "r_err", "type": "result", "config": {"template": "{\"error\": \"veri yok\"}"}}]
        edges = [{"id": "e1", "source": "start", "target": "q"}, {"id": "e2", "source": "q", "target": "cond"},
                 {"id": "e3", "source": "cond", "target": "so", "branch": "true"},
                 {"id": "e4", "source": "cond", "target": "r_err", "branch": "false"},
                 {"id": "e5", "source": "so", "target": "r_ok"}]
        d = _defn(ids["ab-opus"], nodes=nodes, edges=edges,
                  tools={"connector_id": ids["connector"], "allowed_tools": ["execute-dql"]},
                  io={"scheduled_output_format": "json",
                      "output_schema": {"type": "object", "properties": {"severity": {"type": "string"}}}})
        a = await _agent(c, h, "Araç", ["scheduled"], d, publish_h=h)
        det = (await c.get(f"/api/agents/{a['id']}", headers=h)).json()
        assert det["resolved_models"]["so"] == {"profile_id": ids["ab-sonnet"], "profile": "ab-sonnet",
                                                "provider": "anthropic", "model": SONNET, "source": "node"}
        res = await runs.scheduled_run(rt, agent_id=a["id"], schedule_name="t", input_="", created_by="admin")
        assert res["status"] == "completed", res["error"]
        assert res["output"] == {"severity": "high", "summary": "ok"}
        assert [(n["node_id"], n["status"]) for n in res["nodes"]] == [
            ("start", "ok"), ("q", "ok"), ("cond", "ok"), ("so", "ok"), ("r_ok", "ok")]
        assert sent[-1]["model"] == SONNET and sent[-1]["output_config"]["format"]["type"] == "json_schema"
        return True
    assert with_client(api_settings, tmp_path, fn)


# ------------------------------------------------------------------------------------------------ import / export
def test_export_import_roundtrip(api_settings, tmp_path, lg_ready, monkeypatch):
    sent: list[dict] = []
    _mock_llm(monkeypatch, sent)

    async def fn(c, app):
        h = await login(c)
        rt = app.state.rt
        ids = await _seed(rt)
        v = await mkuser(c, h, "ab-viewer", "viewer")
        d = _defn(ids["ab-opus"], tools={"connector_id": ids["connector"], "allowed_tools": ["execute-dql"]})
        a = await _agent(c, h, "Paket", ["chat"], d, publish_h=h)
        assert (await c.get(f"/api/agents/{a['id']}/export", headers=v)).status_code == 403
        r = await c.get(f"/api/agents/{a['id']}/export?version=1", headers=h)
        assert r.status_code == 200 and "attachment" in r.headers["content-disposition"]
        pkg = r.json()
        raw = json.dumps(pkg)
        assert pkg["schema_version"] == 1 and pkg["agent_version"] == 1
        assert ids["ab-opus"] not in raw and ids["connector"] not in raw and "env:SRE_SECRET" not in raw
        assert pkg["definition"]["settings"]["model"]["llm_profile_id"] == "llm:1"
        assert pkg["dependencies"]["llm_profiles"][0]["model"] == OPUS
        assert pkg["dependencies"]["connectors"][0]["tools"][0] == {"name": "execute-dql", "hash": "h-dql"}
        for k in ("conversations", "checkpoints", "runs", "schedules", "messages"):
            assert k not in pkg
        # hassas içerik → export engellenir, alan gösterilir
        det = (await c.get(f"/api/agents/{a['id']}", headers=h)).json()
        leak = copy.deepcopy(det["draft"])
        leak["settings"]["instructions"]["system_prompt"] = "Anahtar: sk-ant-api03-abcdefghijklmnop"
        await c.put(f"/api/agents/{a['id']}/draft", headers=h, json={"revision": det["draft_revision"], "name": "Paket",
                                                                    "modes": ["chat"], "definition": leak})
        r = await c.get(f"/api/agents/{a['id']}/export", headers=h)
        assert r.status_code == 422 and r.json()["error"] == "sensitive_content"
        assert r.json()["details"][0]["field"] == "settings.instructions.system_prompt"
        # içe aktarma önizlemesi: ad, sürüm, mod, node sayısı, eşleştirme önerisi
        assert (await c.post("/api/agents/import/preview", headers=v, json={"package": pkg})).status_code == 403
        pv = (await c.post("/api/agents/import/preview", headers=h, json={"package": pkg})).json()
        assert (pv["agent"]["name"], pv["agent_version"], pv["node_count"], pv["name_conflict"]) == ("Paket", 1, 3, True)
        assert pv["mapping"] == {"llm:1": ids["ab-opus"], "mcp:1": ids["connector"]}
        # uyumsuz model eşleştirmesi (temperature) → ilgili alanda hata, kısmi kayıt yok
        tpkg = copy.deepcopy(pkg)
        tpkg["definition"]["settings"]["model"]["temperature"] = 0.3
        async with transaction(rt.sm) as s:
            n_before = (await s.execute(select(func.count()).select_from(Agent))).scalar()
        r = await c.post("/api/agents/import", headers=h, json={"package": tpkg, "name": "Paket 2",
                                                                "mapping": {"llm:1": ids["ab-sonnet"]}})
        assert r.status_code == 422 and r.json()["details"][0]["field"] == "mapping.llm:1"
        # uyumsuz tool şeması (tanım özeti farklı)
        spkg = copy.deepcopy(pkg)
        spkg["dependencies"]["connectors"][0]["tools"][0]["hash"] = "baska-ozet"
        r = await c.post("/api/agents/import", headers=h, json={"package": spkg, "name": "Paket 2",
                                                                "mapping": {"llm:1": ids["ab-opus"], "mcp:1": ids["connector"]}})
        assert r.status_code == 422 and r.json()["details"][0]["code"] == "mapping_tool_schema"
        # hatalı paket sürümü / geçersiz workflow → red
        r = await c.post("/api/agents/import", headers=h, json={"package": {**pkg, "schema_version": 2}})
        assert r.status_code == 422 and "desteklenmeyen paket sürümü" in r.json()["message"]
        wpkg = copy.deepcopy(pkg)
        wpkg["definition"]["workflow"]["edges"].append({"id": "loop", "source": "result", "target": "start"})
        r = await c.post("/api/agents/import", headers=h, json={"package": wpkg, "name": "Paket 3"})
        assert r.status_code == 422
        async with transaction(rt.sm) as s:
            assert (await s.execute(select(func.count()).select_from(Agent))).scalar() == n_before
        # yeni agent olarak (eksik eşleştirme → taslak; yerleşim ve node kimlikleri korunur)
        r = await c.post("/api/agents/import", headers=h, json={"package": pkg, "name": "Paket (içe)",
                                                                "mapping": {"llm:1": None, "mcp:1": ids["connector"]}})
        assert r.status_code == 201, r.text
        imp = r.json()
        assert imp["agent"]["status"] == "draft" and any(i["code"] == "mapping_missing" for i in imp["issues"])
        idet = (await c.get(f"/api/agents/{imp['agent']['id']}", headers=h)).json()
        assert idet["draft"]["layout"] == pkg["definition"]["layout"]
        assert [(n["id"], n["position"]) for n in idet["draft"]["workflow"]["nodes"]] == \
            [(n["id"], n["position"]) for n in pkg["definition"]["workflow"]["nodes"]]
        assert any(i["code"] == "dependency_missing" for i in idet["issues"])           # test / yayın öncesi
        # mevcut agent'a: yeni taslak revizyonu; soru-cevap yayımlanmış sürümü çalıştırmaya devam eder
        newp = copy.deepcopy(pkg)
        newp["definition"]["settings"]["model"]["llm_profile_id"] = "llm:1"
        r = await c.post("/api/agents/import", headers=h, json={"package": newp, "target": "existing",
                                                                "agent_id": a["id"],
                                                                "mapping": {"llm:1": ids["ab-sonnet"],
                                                                            "mcp:1": ids["connector"]}})
        assert r.status_code == 201, r.text
        after = (await c.get(f"/api/agents/{a['id']}", headers=h)).json()
        assert after["draft_revision"] == det["draft_revision"] + 2 and after["active_version"] == 1
        assert after["has_unpublished_changes"] is True
        n0 = len(sent)
        await _chat(c, h, "hangi model?", agent_id=a["id"])
        assert sent[n0]["model"] == OPUS
        r = await c.post("/api/agents/import", headers=h, json={"package": pkg, "target": "existing",
                                                                "agent_id": str(SYSTEM_AGENT_ID)})
        assert r.status_code == 422
        return True
    assert with_client(api_settings, tmp_path, fn)
