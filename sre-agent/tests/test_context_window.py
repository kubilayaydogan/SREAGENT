"""Context window ölçümü: çağrı başına kayıt, girdi dağılımı, kapasite / doluluk, retry, streaming, context limit."""
from __future__ import annotations

import asyncio
import json

import pytest

try:                                     # Anthropic SDK'nın kullandığı httpx (bu sürümde httpx2)
    import httpx2 as httpx
except ImportError:  # pragma: no cover
    import httpx
from langchain_core.messages import AIMessage, AIMessageChunk

from airlab_agent import context_window as cw
from airlab_agent import middleware as mw


@pytest.fixture
def rows():
    out: list[dict] = []
    cw.set_sink(out.append)
    cw.set_capacity_resolver(None)
    yield out
    cw.set_sink(None)
    cw.set_capacity_resolver(None)


def _cap(capacity=1_000_000, max_output=128_000):
    def fn(model):
        return {"capacity": capacity, "max_output": max_output, "source": "Models API (max_input_tokens)"}
    cw.set_capacity_resolver(fn)
    cw.capacity("claude-opus-5-5", wait=True)


def _msg(inp=1200, out=300, **details):
    u = {"input_tokens": inp, "output_tokens": out, "total_tokens": inp + out}
    if details.get("cache_read") is not None:
        u["input_token_details"] = {"cache_read": details["cache_read"], "cache_creation": 0}
    if details.get("reasoning") is not None:
        u["output_token_details"] = {"reasoning": details["reasoning"]}
    return AIMessage(content="ok", usage_metadata=u, response_metadata={"stop_reason": "end_turn",
                                                                       "model_name": "claude-opus-5-5"})


ANALYSIS = {"risk": {"score": 42}, "mechanisms": [{"id": "row_lock"}], "features_summary": {"db": "ok"}}
RAW_MCP = "x" * 40_000                      # ham MCP çıktısı: modele GÖNDERİLMEZ


def _payload(question="Son 15 dk nasıl?", analysis=ANALYSIS, max_tokens=8000):
    human = f"Kullanıcı sorusu: {question}\n\n<analysis_data>\n{json.dumps(analysis)}\n</analysis_data>\n\nBulguları açıkla."
    return {"model": "claude-opus-5-5", "max_tokens": max_tokens, "system": "Sen SRE asistanısın. " * 20,
            "messages": [{"role": "user", "content": "önceki soru"}, {"role": "assistant", "content": "önceki yanıt"},
                         {"role": "user", "content": human}]}


def _call(task="explain_findings", run=None, payload=None, result=None, exc=None):
    call = mw.Call(kind="model", target="claude-opus-5-5", task=task, request={"messages": []})

    def fn(req):
        cw.on_payload(payload or _payload())
        if exc is not None:
            raise exc
        return result if result is not None else _msg()
    return mw.run_model_sync(call, fn)


def test_ayni_node_iki_cagri_iki_satir_ve_sira(rows):
    """Kabul 1: aynı run içinde analyze node'u iki kez LLM'e giderse iki ayrı satır."""
    _cap()
    run = mw.RunMw(run_id="run-1", kind="analysis", sink=lambda r: None)
    tok = mw.RUN.set(run)
    try:
        _call(task="analyze")
        _call(task="analyze")
    finally:
        mw.RUN.reset(tok)
    assert len(rows) == 2
    assert [r["call_seq"] for r in rows] == [1, 2]
    assert {r["node"] for r in rows} == {"analyze"} and {r["run_id"] for r in rows} == {"run-1"}
    assert rows[0]["logical_call_id"] != rows[1]["logical_call_id"]
    assert all(r["status"] == "ok" and r["attempt_count"] == 1 for r in rows)


def test_dagilim_gonderilen_ozeti_sayar_ham_mcp_ciktisini_saymaz(rows):
    """Kabul 2: MCP çıktısı özetlenerek gönderildiyse dağılımda gönderilen özetin token sayısı yer alır."""
    _cap()
    cw.note_analysis_payload(ANALYSIS)
    _call(payload=_payload())
    r = rows[0]
    comps = {c["key"]: c for c in r["components"]}
    block = f"<analysis_data>\n{json.dumps(ANALYSIS)}\n</analysis_data>"
    assert comps["tool_results"]["tokens"] == cw.estimate_tokens(block)
    assert comps["tool_results"]["tokens"] < cw.estimate_tokens(RAW_MCP) / 50
    assert comps["tool_results"]["method"] == "estimate"
    assert "alan:risk" in comps["tool_results"]["refs"]
    assert comps["history"]["tokens"] > 0 and comps["system"]["tokens"] > 0 and comps["user_message"]["tokens"] > 0
    # sağlayıcı toplamı − tahmin = Diğer (çifte sayım yok)
    assert comps["other"]["method"] == "provider_minus_estimate"
    assert sum(c["tokens"] for c in r["components"]) == max(r["input_tokens"], r["estimated_input"])
    dec = r["context_decisions"][0]
    assert dec["status"] == "summarized" and "Dynatrace MCP" in dec["summarized"][0]["ref"]


def test_kapasite_bilinmiyorsa_doluluk_hesaplanmaz(rows):
    """Kabul 3: kapasite bilinmiyorsa yüzde yok (None, sıfır değil)."""
    _call()
    r = rows[0]
    assert r["capacity"] is None and r["capacity_source"] == "unknown"
    assert r["planned_fill"] is None and r["actual_fill"] is None
    assert r["input_tokens"] == 1200                         # sağlayıcı ölçümü yine kayıtlı


def test_planlanan_ve_gerceklesen_doluluk_ayri(rows):
    _cap(capacity=100_000)
    _call(payload=_payload(max_tokens=8000), result=_msg(inp=20_000, out=1_000, cache_read=5_000, reasoning=400))
    r = rows[0]
    assert r["planned_fill"] == pytest.approx((r["estimated_input"] + 8000) / 100_000)
    assert r["actual_fill"] == pytest.approx(21_000 / 100_000)
    assert r["cache_read_tokens"] == 5_000 and r["reasoning_tokens"] == 400     # alt kalem: toplamlar değişmez
    assert r["input_tokens"] == 20_000 and r["output_tokens"] == 1_000
    assert r["reserved_output"] == 8000 and r["model_max_output"] == 128_000


def test_usage_eksikse_sifir_yazilmaz(rows):
    """Kabul 4: usage bilgisi eksik çağrı hata üretmez; değerler None."""
    _cap()
    _call(result=AIMessage(content="ok"))
    r = rows[0]
    assert r["usage_source"] == "missing" and r["input_tokens"] is None and r["actual_fill"] is None
    assert {c["key"]: c for c in r["components"]}["other"]["method"] == "unknown"


def test_context_limit_hatalari_ayri(rows):
    """Kabul 5: context limit hatası genel hatadan ayrı; stop_reason ile kesilme de context_limit."""
    class BadRequest(Exception):
        status_code = 400
    with pytest.raises(BadRequest):
        _call(exc=BadRequest("prompt is too long: 1200000 tokens > 1000000 maximum"))
    with pytest.raises(RuntimeError):
        _call(exc=RuntimeError("bağlantı koptu"))
    m = _msg()
    m.response_metadata["stop_reason"] = "model_context_window_exceeded"
    _call(result=m)
    assert [(r["status"], r["error_code"]) for r in rows] == [
        ("context_limit", "prompt_too_long"), ("error", "RuntimeError"),
        ("context_limit", "model_context_window_exceeded")]
    assert rows[0]["http_status"] == 400


def test_streaming_final_usage(rows):
    """Kabul 5: streaming çağrı final usage ile kaydedilir; chunk'lar aynen iletilir."""
    _cap()
    call = mw.Call(kind="model", target="claude-opus-5-5", task="answer", request={"messages": [], "stream": True})

    async def gen():
        cw.on_payload(_payload())
        yield AIMessageChunk(content="", usage_metadata={"input_tokens": 900, "output_tokens": 0, "total_tokens": 900})
        yield AIMessageChunk(content="Mer")
        yield AIMessageChunk(content="haba", usage_metadata={"input_tokens": 0, "output_tokens": 50, "total_tokens": 50},
                             response_metadata={"stop_reason": "end_turn"})

    async def go():
        return [c.content async for c in mw.stream_model(call, lambda req: gen())]
    assert asyncio.run(go()) == ["", "Mer", "haba"]
    r = rows[0]
    assert r["streaming"] and r["input_tokens"] == 900 and r["output_tokens"] == 50
    assert r["stream"]["chunks"] == 3 and r["stream"]["completed"] and r["status"] == "ok"


def test_sink_hatasi_cagriyi_bozmaz():
    def boom(_):
        raise RuntimeError("db yok")
    cw.set_sink(boom)
    try:
        lost = cw.stats()["lost"]
        assert _call().content == "ok"
        assert cw.stats()["lost"] == lost + 1
    finally:
        cw.set_sink(None)


def test_engellenen_cagri_kayit_uretmez(rows):
    """Middleware'in engellediği çağrı sağlayıcıya gitmez → context window kaydı yok."""
    from airlab_agent.llm import RUN_BUDGET, LlmBudgetExceeded, RunBudget
    t = RUN_BUDGET.set(RunBudget(max_calls=0, max_tokens=10))
    try:
        with pytest.raises(LlmBudgetExceeded):
            _call()
    finally:
        RUN_BUDGET.reset(t)
    assert rows == []


def test_gercek_sdk_retry_denemeleri_ayri_satir(rows):
    """Kabul 5: Anthropic SDK retry'ı (529 → 200) iki satır; aynı logical_call_id, ilk deneme 'retried'.
    Gerçek TracedChatAnthropic + SDK; ağ yerine httpx MockTransport."""
    from airlab_agent.agent_trace import traced_chat_anthropic_cls
    hits = []

    def handler(request: httpx.Request) -> httpx.Response:
        hits.append(request.headers.get("x-stainless-retry-count"))
        if len(hits) == 1:
            return httpx.Response(529, json={"type": "error", "error": {"type": "overloaded_error", "message": "x"}},
                                  headers={"retry-after-ms": "10"})
        body = json.loads(request.content)
        assert "x" * 1000 not in json.dumps(body)               # yalnız gönderilen içerik
        return httpx.Response(200, headers={"request-id": "req_test_1"}, json={
            "id": "msg_1", "type": "message", "role": "assistant", "model": "claude-opus-5-5",
            "content": [{"type": "text", "text": "tamam"}], "stop_reason": "end_turn", "stop_sequence": None,
            "usage": {"input_tokens": 700, "output_tokens": 12, "cache_read_input_tokens": 100,
                      "cache_creation_input_tokens": 0}})

    cls = traced_chat_anthropic_cls()
    chat = cls(model="claude-opus-5-5", api_key="test-only-key", max_retries=2, max_tokens=512,
               base_url="http://cw-test.invalid")
    assert cw.instrument(chat)
    hc = chat._client._client
    hc._transport = httpx.MockTransport(handler)
    hc._mounts = {}                                          # ortam proxy mount'ları da devre dışı
    _cap()
    call = mw.Call(kind="model", target="claude-opus-5-5", task="route_question", request={"messages": []})
    out = mw.run_model_sync(call, lambda req: chat.invoke([("system", "sistem"), ("human", "soru")]))
    assert out.content == "tamam"
    assert hits == ["0", "1"]
    assert [r["status"] for r in rows] == ["retried", "ok"]
    assert rows[0]["logical_call_id"] == rows[1]["logical_call_id"]
    assert [r["attempt_no"] for r in rows] == [0, 1] and rows[0]["attempt_count"] == 2
    assert rows[0]["error_code"] == "overloaded" and rows[0]["http_status"] == 529
    r = rows[1]
    assert r["input_tokens"] == 800 and r["cache_read_tokens"] == 100      # input_tokens + cache (sağlayıcı tanımı)
    assert r["reserved_output"] == 512 and r["request_id"] == "req_test_1"
    assert {c["key"] for c in r["components"]} >= {"system", "user_message", "other"}
