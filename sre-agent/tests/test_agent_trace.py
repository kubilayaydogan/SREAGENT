"""Agent Trace kaydedicisi (airlab_agent.agent_trace).

1. Analiz graph'ı: collect → analyze State In / Node Update / State Out ayrı; geçişler checkpoint.next'ten; routing
   kararı (needs_extra) koşullu kenar callback'inden; tekrar çalışan node'un attempt'i; LLM (explain) framework
   payload'ı; updates tüketicisi (on_node) değişmedi.
2. Gerçek ChatAnthropic adaptörü (_create sahte): Provider payload request/response, structured output parser girdisi,
   doğrulanmış çıktı, iç içe olmayan alanda ValidationError ve takip eden retry.
3. Paralel node'lar aynı graph adımında; birleşik state adım düzeyinde.
4. Maskeleme: gizli değerler hiçbir olayda yok; orijinal state değişmedi. Kayıt hatası agent'ı durdurmaz.
"""
from __future__ import annotations

import json
import operator
from types import SimpleNamespace as NS
from typing import Annotated, TypedDict

import pytest
from synth import cfg, windows
from test_trace_observability import PLAIN, SecretLlm, _setup

from airlab_agent.agent_trace import (STREAM_MODES, TraceRecorder, safe_copy, state_diff, traced_chat_anthropic_cls,
                                      validation_details, with_callback, ACTIVE)
from airlab_agent.graph import run_analysis

SECRET_KEY = "sk-ant-api03-" + "Q" * 32


def rec_sink():
    evs: list[dict] = []
    return evs, evs.append


def by(evs, t, **kw):
    return [e for e in evs if e["event_type"] == t and all(e.get(k) == v for k, v in kw.items())]


def test_analysis_graph_nodes_state_transitions_llm(tmp_path):
    settings, factory, start, end, _, _, _ = _setup(tmp_path, {})
    evs, sink = rec_sink()
    rec = TraceRecorder("rep-1", sink, graph="airlab-sre-analysis")
    seen: list[tuple] = []
    out = run_analysis(settings, factory, start, end, llm=SecretLlm(), question=f"neden? {SECRET_KEY}",
                       persist_files=False, run_id="rep-1", trace_recorder=rec,
                       on_node=lambda n, u: seen.append((n, sorted(u))))
    assert out["report"]["status"] in ("complete", "partial")
    # updates tüketicisi aynı biçimde
    assert [n for n, _ in seen][:3] == ["plan", "collect", "analyze"] and seen[-1][0] == "finalize"
    seqs = [e["sequence"] for e in evs]
    assert seqs == list(range(1, len(evs) + 1))                                 # kararlı, boşluksuz sıra
    starts = {e["node"]: e for e in by(evs, "node_start")}
    ends = {e["node"]: e for e in by(evs, "node_end")}
    states = {e["step"]: e for e in by(evs, "state_out")}
    c_end, a_start, a_end = ends["collect"], starts["analyze"], ends["analyze"]
    # collect sonrası results (State Out) = analyze girişi (State In); Node Update ayrı tür
    after_collect = states[c_end["step"]]["payload"]["values"]
    assert after_collect["results"] == a_start["payload"]["state_in"]["results"]
    assert "results" in c_end["payload"]["node_update"] and "plan" not in c_end["payload"]["node_update"]
    assert "attribution" not in after_collect and "attribution" in states[a_end["step"]]["payload"]["values"]
    assert states[c_end["step"]]["payload"]["node_exec_ids"] == [c_end["node_exec_id"]]
    # diff: analyze'nin eklediği alanlar
    diff = state_diff(a_start["payload"]["state_in"], states[a_end["step"]]["payload"]["values"])
    assert any(d["path"].startswith("attribution") and d["change"] == "added" for d in diff)
    # geçişler ve routing kararı
    tr = {tuple(e["payload"]["from"]): e["payload"] for e in by(evs, "transition")}
    assert tr[("collect",)]["to"] == ["analyze"] and tr[("collect",)]["routing"] is None
    assert tr[("analyze",)]["routing"]["analyze"][0]["function"] == "needs_extra"
    assert tr[("analyze",)]["to"] == [tr[("analyze",)]["routing"]["analyze"][0]["result"]]
    # tekrar çalışan node: extra_evidence attempt'leri artar (row_lock senaryosunda en az bir kez)
    ex = by(evs, "node_start", node="extra_evidence")
    assert [e["attempt"] for e in ex] == list(range(1, len(ex) + 1)) and len(ex) >= 1
    # stream olayları: tüketilen modlar
    modes = {(e["payload"]["mode"], e["payload"].get("type")) for e in by(evs, "stream")}
    assert {("updates", None), ("debug", "task"), ("debug", "task_result"), ("debug", "checkpoint")} <= modes
    # LLM: explain çağrısı explain node'una bağlı, framework payload
    req = by(evs, "llm_request")
    resp = by(evs, "llm_response")
    assert len(req) == len(resp) == 1 and req[0]["node"] == "explain" and req[0]["call_id"] == resp[0]["call_id"]
    assert req[0]["payload"]["source"] == "framework" and req[0]["payload"]["messages"][0]["role"] == "user"
    assert resp[0]["payload"]["provider_response_status"] == "not_captured"
    # maskeleme
    blob = json.dumps(evs, ensure_ascii=False)
    assert SECRET_KEY not in blob and "dqlQueryString" not in blob
    assert all('"…"' in e["payload"]["values"]["results"]["http"]["dql"] for e in by(evs, "state_out")
               if "results" in (e["payload"].get("values") or {}))
    assert SECRET_KEY in out["question"]                                          # agent state'i değişmedi
    assert rec.capture_errors == 0


# ------------------------------------------------------------------ gerçek adaptör + structured output
def _anthropic_message(text: str):
    from anthropic.types import Message
    return Message.model_validate({
        "id": "msg_test", "type": "message", "role": "assistant", "model": "claude-test",
        "content": [{"type": "text", "text": text}], "stop_reason": "end_turn", "stop_sequence": None,
        "usage": {"input_tokens": 11, "output_tokens": 7}})


def test_provider_payload_parser_validation_error_and_retry():
    from langgraph.checkpoint.memory import InMemorySaver
    from langgraph.graph import END, START, StateGraph
    from pydantic import BaseModel, Field

    class Inner(BaseModel):
        minutes: int = Field(ge=1)

    class Route(BaseModel):
        intent: str
        window: Inner

    replies = iter([json.dumps({"intent": "analyze", "window": {"minutes": 0}, "note": f"{SECRET_KEY}"}),
                    json.dumps({"intent": "analyze", "window": {"minutes": 30}})])
    Traced = traced_chat_anthropic_cls()
    llm = Traced(model="claude-test", api_key="sk-ant-api03-" + "K" * 32, max_tokens=100, max_retries=0)
    sent: list[dict] = []

    def fake_create(payload):
        sent.append(payload)
        return NS(parse=lambda: _anthropic_message(next(replies)))
    object.__setattr__(llm, "_create", fake_create)

    class S(TypedDict, total=False):
        q: str
        route: dict

    def route_node(state):
        r = llm.with_structured_output(Route, include_raw=True, method="json_schema")
        for _ in range(2):                                   # uygulama düzeyinde 1 retry
            out = r.invoke([("system", "ayrıştır"), ("human", state["q"])], config={"run_name": "route_question"})
            if out["parsed"] is not None:
                return {"route": out["parsed"].model_dump()}
        return {"route": {}}

    g = StateGraph(S)
    g.add_node("route", route_node)
    g.add_edge(START, "route")
    g.add_edge("route", END)
    graph = g.compile(checkpointer=InMemorySaver())
    evs, sink = rec_sink()
    rec = TraceRecorder("chat-1", sink)
    cfg_ = with_callback({"configurable": {"thread_id": "t"}}, rec)
    tok = ACTIVE.set(rec)
    try:
        for mode, chunk in graph.stream({"q": f"son yarım saat, anahtar {SECRET_KEY}"}, cfg_, stream_mode=STREAM_MODES):
            rec.on_stream(mode, chunk)
    finally:
        ACTIVE.reset(tok)
    assert graph.get_state(cfg_).values["route"] == {"intent": "analyze", "window": {"minutes": 30}}
    assert len(sent) == 2

    reqs, preq, resps = by(evs, "llm_request"), by(evs, "llm_provider_request"), by(evs, "llm_response")
    assert [e["attempt"] for e in reqs] == [1, 2] and all(e["node"] == "route" for e in reqs)
    assert reqs[0]["payload"]["task"] == "route_question" and reqs[0]["payload"]["structured_output"]
    # Provider payload: sağlayıcıya giden JSON ve sağlayıcı yanıtı aynı çağrı kimliğiyle
    assert [e["call_id"] for e in preq] == [e["call_id"] for e in reqs]
    assert preq[0]["payload"]["payload"]["model"] == "claude-test" and preq[0]["payload"]["payload"]["messages"]
    assert resps[0]["payload"]["provider_response_status"] == "captured"
    assert resps[0]["payload"]["provider_response"]["usage"]["input_tokens"] == 11
    assert resps[0]["payload"]["finish_reason"] == "end_turn"
    # Parser: girdi metin + JSON; ilk deneme alan yolu korunmuş ValidationError, ikinci doğrulanmış çıktı
    pin, err, pout = by(evs, "parser_input"), by(evs, "validation_error"), by(evs, "parser_output")
    assert len(pin) == 2 and pin[0]["payload"]["json"]["window"] == {"minutes": 0}
    assert isinstance(pin[0]["payload"]["text"], str)
    assert pin[0]["related_call_id"] == reqs[0]["call_id"] and pin[1]["related_call_id"] == reqs[1]["call_id"]
    paths = {x["path"] for x in err[0]["payload"]["errors"]}
    assert "window.minutes" in paths and err[0]["payload"]["error_type"] == "ValidationError"
    assert err[0]["related_call_id"] == reqs[0]["call_id"]
    assert pout[0]["payload"]["model"] == "Route" and pout[0]["payload"]["validated"]["window"]["minutes"] == 30
    assert err[0]["sequence"] < reqs[1]["sequence"]                         # retry hatadan sonra
    blob = json.dumps(evs, ensure_ascii=False)
    assert SECRET_KEY not in blob and "K" * 32 not in blob


def test_parallel_nodes_grouped_by_step():
    from langgraph.checkpoint.memory import InMemorySaver
    from langgraph.graph import END, START, StateGraph

    class S(TypedDict, total=False):
        log: Annotated[list, operator.add]

    g = StateGraph(S)
    g.add_node("a", lambda s: {"log": ["a"]})
    g.add_node("b", lambda s: {"log": ["b"]})
    g.add_node("c", lambda s: {"log": ["c"]})
    g.add_edge(START, "a"), g.add_edge("a", "b"), g.add_edge("a", "c"), g.add_edge("b", END), g.add_edge("c", END)
    graph = g.compile(checkpointer=InMemorySaver())
    evs, sink = rec_sink()
    rec = TraceRecorder("p", sink)
    for mode, chunk in graph.stream({"log": []}, {"configurable": {"thread_id": "p"}}, stream_mode=STREAM_MODES):
        rec.on_stream(mode, chunk)
    b, c = by(evs, "node_end", node="b")[0], by(evs, "node_end", node="c")[0]
    assert b["step"] == c["step"]
    assert b["payload"]["node_update"] == {"log": ["b"]} and c["payload"]["node_update"] == {"log": ["c"]}
    st = [e for e in by(evs, "state_out") if e["step"] == b["step"]][0]["payload"]
    assert st["combined"] is True and set(st["node_exec_ids"]) == {b["node_exec_id"], c["node_exec_id"]}
    assert sorted(st["values"]["log"]) == ["a", "b", "c"]


def test_masking_truncation_and_sink_failure():
    big = {"password": "hunter2", "nested": {"api_key": "abc123", "Authorization": "Bearer xyz"},
           "text": f"key {SECRET_KEY} mail ali@example.com", "dql": 'fetch logs | filter x == "gizli-deger"',
           "items": list(range(500)), "records": [{"content": "log satırı PLAIN-X", "ts": 1}], "long": "x" * 50_000, "input_tokens": 12}
    data, trunc, size = safe_copy(big)
    s = json.dumps(data, ensure_ascii=False)
    for v in ("hunter2", "abc123", "Bearer xyz", SECRET_KEY, "ali@example.com", "gizli-deger"):
        assert v not in s
    assert data["input_tokens"] == 12 and trunc and data["items"][-1] == {"_truncated_items": 300}
    assert data["records"] == {"_records_omitted": True, "count": 1, "fields": ["content", "ts"],
                               "reason": "ham telemetri trace kopyasına alınmaz"} and "PLAIN-X" not in s
    assert big["password"] == "hunter2" and len(big["items"]) == 500             # orijinal değişmedi
    huge, t2, _ = safe_copy({"k": ["y" * 15_000 for _ in range(100)]})
    assert t2 and huge["_truncated"] is True and len(huge["preview"]) <= 16_000

    def boom(_ev):
        raise RuntimeError("db down")
    rec = TraceRecorder("x", boom)
    rec.emit("node_start", {"a": 1})                                             # hata yutulur ve sayılır
    assert rec.capture_errors == 1
    assert validation_details(ValueError("düz hata"))["errors"] == []


def test_recorder_failure_does_not_stop_analysis(tmp_path):
    settings, factory, start, end, _, _, _ = _setup(tmp_path, {})
    base = run_analysis(settings, factory, start, end, persist_files=False, run_id="r-a", use_llm=False)

    def boom(_ev):
        raise RuntimeError("kayıt deposu yok")
    rec = TraceRecorder("r-b", boom)
    out = run_analysis(settings, factory, start, end, persist_files=False, run_id="r-b", use_llm=False,
                       trace_recorder=rec)
    assert out["report"]["risk"] == base["report"]["risk"] and out["status"] == base["status"]
    assert rec.capture_errors > 0
