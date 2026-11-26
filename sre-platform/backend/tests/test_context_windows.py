"""Context Windows (backend → API): gerçek ChatOrchestrator + gerçek ClaudePort + gerçek TracedChatAnthropic / Anthropic
SDK; ağ yerine httpx MockTransport (sağlayıcı yanıtı test düzeneğidir). Gerçek PostgreSQL.

Doğrulananlar
* Sohbet turu iki gerçek LLM çağrısı yapar (route + streaming answer) → iki satır, node ve çağrı sırasıyla; streaming
  çağrının final usage'ı kayıtlıdır; dağılımda RAG / memory bloğu ve context kararları görünür.
* Kapasite bilinmiyorsa doluluk None; usage eksik eski kayıtlar ve retry / context limit satırları API'de hatasız döner.
* Sıralama (en yüksek doluluk), filtreler, sayfalama, detay (deneme kardeşleri, bağlantılar), görünürlük (chat_turn
  yalnız sahibine), RBAC (viewer 403), retention.
"""
from __future__ import annotations

import json
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace as NS

ROOT = Path(__file__).resolve().parents[3]
sys.path[:0] = [str(ROOT / "sre-agent/tests"), str(ROOT / "sre-agent/eval")]

from sqlalchemy import func, select, update  # noqa: E402
from test_agent_memory import _ctx  # noqa: E402
from test_api import api_settings, lg_ready, login, mkuser, with_client  # noqa: E402,F401

from airlab_agent import context_window as cw  # noqa: E402
from sre_platform.agent.chat import ChatOrchestrator  # noqa: E402
from sre_platform.agent.llm_port import ClaudePort  # noqa: E402
from sre_platform.context_window.writer import to_row  # noqa: E402
from sre_platform.db.models import LlmContextCall, Role  # noqa: E402
from sre_platform.db.session import transaction  # noqa: E402
from sre_platform.repositories import conversations, users  # noqa: E402

try:
    import httpx2 as hx
except ImportError:  # pragma: no cover
    import httpx as hx

MODEL = "claude-opus-5-5"
ROUTE = {"intent": "history", "minutes": None, "start_utc": None, "end_utc": None, "focus_apps": [],
         "history_scope": ["investigations"], "since_days": None, "mechanism": None, "search_terms": "checkin"}


def _sse(events: list[dict]) -> bytes:
    return "".join(f"event: {e['type']}\ndata: {json.dumps(e)}\n\n" for e in events).encode()


def _handler(sent: list[dict]):
    def h(request):
        body = json.loads(request.content)
        sent.append(body)
        if body.get("stream"):
            return hx.Response(200, headers={"content-type": "text/event-stream", "request-id": "req_stream"},
                               content=_sse([
                {"type": "message_start", "message": {"id": "msg_2", "type": "message", "role": "assistant",
                                                      "model": MODEL, "content": [], "stop_reason": None,
                                                      "stop_sequence": None,
                                                      "usage": {"input_tokens": 1500, "output_tokens": 1}}},
                {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}},
                {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "Kayıt yok."}},
                {"type": "content_block_stop", "index": 0},
                {"type": "message_delta", "delta": {"stop_reason": "end_turn", "stop_sequence": None},
                 "usage": {"input_tokens": 1500, "output_tokens": 20}},   # gerçek API: kümülatif
                {"type": "message_stop"}]))
        return hx.Response(200, headers={"request-id": "req_route"}, json={
            "id": "msg_1", "type": "message", "role": "assistant", "model": MODEL,
            "content": [{"type": "text", "text": json.dumps(ROUTE)}], "stop_reason": "end_turn",
            "stop_sequence": None, "usage": {"input_tokens": 600, "output_tokens": 40}})
    return h


def _real_port(sent):
    from airlab_agent.agent_trace import traced_chat_anthropic_cls
    chat = traced_chat_anthropic_cls()(model=MODEL, api_key="test-only-key", max_retries=0, max_tokens=4096,
                                       base_url="http://cw-platform-test.invalid")
    assert cw.instrument(chat)
    hc = chat._async_client._client
    hc._transport, hc._mounts = hx.MockTransport(_handler(sent)), {}
    core = NS(chat=chat, rca_chat=chat, callbacks=[], model_name=MODEL, rca_model_name=MODEL,
              settings=NS(claude_model=MODEL), _track=lambda m: None)
    return ClaudePort(core)


def _synthetic(**kw) -> dict:
    """Test düzeneği satırı (yalnız test veritabanında; gerçek telemetri olarak gösterilmez)."""
    lid = str(uuid.uuid4())
    base = {"call_id": lid, "logical_call_id": lid, "attempt_no": 0, "attempt_count": 1, "run_id": "fixture-run",
            "run_kind": "analysis", "node": "explain", "task": "explain_findings", "call_seq": 1,
            "provider": "anthropic", "model": MODEL, "streaming": False, "started_at": datetime.now(timezone.utc),
            "status": "ok", "usage_source": "missing"}
    return to_row({**base, **kw})


def test_chat_turn_end_to_end_records_and_api(api_settings, tmp_path, lg_ready):
    sent: list[dict] = []

    async def fn(c, app):
        h = await login(c)
        rt = app.state.rt
        rt.llm = _real_port(sent)
        cw.set_capacity_resolver(None)                    # kapasite kaynağı yok → doluluk "Bilinmiyor"
        async with transaction(rt.sm) as s:
            u = await users.create(s, "cw-owner", "SRE", Role.SRE, "parola-test-12345")
            conv = await conversations.create(s, u.id, "cw")
            uid, cid, tid = u.id, conv.id, conv.thread_id
        ctx, _ = _ctx(uid, cid, tid)
        await ChatOrchestrator(rt).turn(ctx, "Checkin daha önce yavaşladı mı?")
        await rt.cw_writer.flush()
        run_id = f"chat-{ctx.result['user_message_id']}"
        lst = (await c.get("/api/context-windows/calls", headers=h,
                           params={"run_id": run_id, "sort": "sequence"})).json()
        det = (await c.get(f"/api/context-windows/calls/{lst['items'][1]['call_id']}", headers=h)).json()
        ov = (await c.get("/api/context-windows/overview", headers=h)).json()
        h2 = await mkuser(c, h, "cw-other", "sre")
        other = await c.get(f"/api/context-windows/calls/{lst['items'][0]['call_id']}", headers=h2)
        other_list = (await c.get("/api/context-windows/calls", headers=h2, params={"run_id": run_id})).json()
        return lst, det, ov, other.status_code, other_list

    lst, det, ov, other_code, other_list = with_client(api_settings, tmp_path, fn)
    assert len(sent) == 2 and sent[1]["stream"] is True
    assert [(i["node"], i["call_seq"], i["streaming"]) for i in lst["items"]] == [
        ("route", 1, False), ("respond", 2, True)]
    route, ans = lst["items"]
    assert route["input_tokens"] == 600 and route["output_tokens"] == 40 and route["status"] == "ok"
    assert route["actual_fill"] is None and route["capacity"] is None            # kapasite bilinmiyor
    assert ans["input_tokens"] == 1500 and ans["output_tokens"] == 20             # streaming final usage
    comps = {x["key"]: x for x in det["components"]}
    assert comps["rag"]["tokens"] > 0 and "<memory_data>" in comps["rag"]["refs"]
    assert comps["system"]["tokens"] > 0 and comps["other"]["method"] == "provider_minus_estimate"
    assert det["reserved_output"] == 4096 and det["capacity_source"] == "unknown"
    assert det["request_id"] == "req_stream" and det["stream"]["completed"] is True
    assert any(d["section"] == "memory_data" for d in det["context_decisions"])
    assert det["links"]["agent_trace"].startswith(f"/agent-trace/{lst['items'][1]['run_id']}")
    assert ov["calls"] >= 2 and ov["context_limit_errors"] == 0
    # ham içerik dönmez
    assert "Kayıt yok." not in json.dumps(det) and "Checkin daha önce" not in json.dumps(det)
    assert other_code == 404 and other_list["total"] == 0                       # chat_turn yalnız sahibine


def test_legacy_retry_context_limit_sort_filters_rbac_retention(api_settings, tmp_path):
    async def fn(c, app):
        h = await login(c)
        rt = app.state.rt
        lid = str(uuid.uuid4())
        rows = [
            _synthetic(run_id="legacy-run", node="interpret", task="interpret_question"),   # usage / kapasite yok
            _synthetic(call_id=str(uuid.uuid4()), logical_call_id=lid, attempt_no=0, attempt_count=2,
                       status="retried", error_code="overloaded", http_status=529),
            _synthetic(call_id=lid, logical_call_id=lid, attempt_no=1, attempt_count=2, status="ok",
                       usage_source="provider", input_tokens=50_000, output_tokens=2_000, capacity=1_000_000,
                       actual_fill=0.052, planned_fill=0.06, estimated_input=48_000, reserved_output=12_000,
                       components=[{"key": "tool_results", "label": "MCP / tool sonuçları", "tokens": 30_000,
                                    "method": "estimate", "refs": []}]),
            _synthetic(status="context_limit", error_code="prompt_too_long", http_status=400, node="respond",
                       task="answer", usage_source="missing"),
            _synthetic(usage_source="provider", input_tokens=500_000, output_tokens=1, capacity=1_000_000,
                       actual_fill=0.5, node="route", task="route_question",
                       started_at=datetime.now(timezone.utc) - timedelta(days=200)),            # retention
        ]
        async with transaction(rt.sm) as s:
            for r in rows:
                s.add(LlmContextCall(**r))
        ov = (await c.get("/api/context-windows/overview", headers=h, params={"range": "24h"})).json()
        by_fill = (await c.get("/api/context-windows/calls", headers=h, params={"sort": "fill"})).json()
        retried = (await c.get(f"/api/context-windows/calls/{lid}", headers=h)).json()
        legacy = (await c.get(f"/api/context-windows/calls/{rows[0]['call_id']}", headers=h)).json()
        flt = (await c.get("/api/context-windows/calls", headers=h, params={"status": "context_limit"})).json()
        page = (await c.get("/api/context-windows/calls", headers=h, params={"limit": 2, "offset": 2})).json()
        facets = (await c.get("/api/context-windows/facets", headers=h)).json()
        bad = await c.get("/api/context-windows/calls/not-a-uuid", headers=h)
        hv = await mkuser(c, h, "cw-viewer", "viewer")
        denied = await c.get("/api/context-windows/calls", headers=hv)
        anon = await c.get("/api/context-windows/overview")
        from sre_platform.worker import retention
        res = await retention(rt, {})
        async with transaction(rt.sm) as s:
            left = (await s.execute(select(func.count()).select_from(LlmContextCall))).scalar_one()
        return (ov, by_fill, retried, legacy, flt, page, facets, bad.status_code, denied.status_code,
                anon.status_code, res, left)

    (ov, by_fill, retried, legacy, flt, page, facets, bad, denied, anon, res,
     left) = with_client(api_settings, tmp_path, fn)
    assert ov["context_limit_errors"] == 1 and ov["retried_attempts"] == 1
    assert ov["max_actual_fill"] == 0.052 and ov["calls"] == 3 and ov["attempts"] == 4   # 200 gün önceki aralık dışı
    assert ov["usage_missing"] == 2 and ov["max_call"]["call_id"] == retried["call_id"]
    fills = [i["actual_fill"] for i in by_fill["items"]]
    assert fills[0] == 0.052 and all(f is None for f in fills[1:])        # en yüksek doluluk önce, NULL'lar sonda
    assert [a["status"] for a in retried["attempts"]] == ["retried", "ok"]
    assert retried["components"][0]["tokens"] == 30_000
    assert legacy["input_tokens"] is None and legacy["actual_fill"] is None and legacy["components"] is None
    assert legacy["context_decisions"] is None and legacy["links"]["agent_trace"] == "/agent-trace/legacy-run"
    assert legacy["links"]["agent_trace_level"] == "run"
    assert [i["status"] for i in flt["items"]] == ["context_limit"]
    assert page["total"] == 4 and len(page["items"]) == 2
    assert "explain" in facets["nodes"] and facets["models"] == [MODEL]
    assert res["llm_context_calls"] == 1 and left == 4                    # llm_runs_days (180) dışı silindi
    assert bad == 404 and denied == 403 and anon == 401
