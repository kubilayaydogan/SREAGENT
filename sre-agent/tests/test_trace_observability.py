"""LangSmith trace'lerinin production debugging için doğrulanması (fixture: gerçek langsmith Client, ağ yerine
yakalama — tracecap.Capture).

1. Kök trace tek; düğümler sabit adlı (plan, collect, analyze, explain, finalize); trigger/run_id metadata'da.
2. Paralel MCP çağrıları doğru düğüm (collect) ve kök altında; retry'lar aynı mcp_call_id span'inde ayırt edilir.
3. MCP timeout'u hatalı span olarak görünür; kısmi rapor finalize özetinde ayrıca anlaşılır.
5. Sentetik gizli değerler (allowlist dışı düz metin dahil) payload'a, nested span'lere ve hata metnine sızmaz.
6. Tracing kapalıyken / LangSmith erişilemezken analiz çıktısı korunur.
(4 — checkpoint sonrası devam: platform testi test_trace_links.py)
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import time
import uuid
from types import SimpleNamespace as NS

import pytest
from synth import build, cfg, windows
from test_scenarios import SCENARIOS
from tracecap import Capture

from airlab_agent.config import Settings
from airlab_agent.features import window_list
from airlab_agent.graph import run_analysis
from airlab_agent.mcp_gateway import McpGateway
from airlab_agent.signals import CATALOG, build_queries
from airlab_agent.tracing import child_config, trace_safe, without_tracing

PLAIN = "PLAIN-SECRET-VALUE-42"                       # hiçbir redaksiyon kalıbına uymayan gizli değer
ANTHROPIC = "sk-ant-api03-" + "Z" * 32
QUESTION = f"Checkin neden yavaş? müşteri ali@example.com, not: {PLAIN}, key {ANTHROPIC}"
NODES = ("plan", "collect", "analyze", "explain", "finalize")


class SecretLlm:
    """explain çağrısı gerçek bir LangChain chat model run'ı üretir; prompt ve çıktı gizli değer içerir."""
    rca_model_name = "fake-rca"

    def __init__(self):
        self.usage = {"input_tokens": 0, "output_tokens": 0, "calls": 0}

    def explain(self, payload, question):
        from langchain_core.language_models.fake_chat_models import FakeListChatModel
        m = FakeListChatModel(responses=[f"Kök neden row lock. {PLAIN} {ANTHROPIC}"])
        msg = m.invoke([("human", f"{question} {json.dumps(payload, default=str)[:200]} {PLAIN}")],
                       config=child_config(None, "explain_findings", metadata={"model": self.rca_model_name}))
        return msg.content


class FakeSession:
    """MCP oturumu: anahtar başına davranış listesi (deneme sırasına göre): ok | timeout | perm | empty."""

    def __init__(self, dql_key: dict[str, str], results: dict, plan: dict[str, list[str]]):
        self.dql_key, self.results, self.plan, self.n = dql_key, results, plan, {}
        self.active = self.peak = 0

    async def call_tool(self, name, args):
        dql = args["dqlQueryString"]
        key = self.dql_key.get(dql, "extra")
        i = self.n.get(key, 0)
        self.n[key] = i + 1
        steps = self.plan.get(key, ["ok"])
        beh = steps[min(i, len(steps) - 1)]
        self.active += 1
        self.peak = max(self.peak, self.active)
        try:
            await asyncio.sleep(0.01)
            if beh == "timeout":
                await asyncio.sleep(5)
            if beh == "perm":
                return NS(isError=True, content=[NS(text=f"403 forbidden {PLAIN}")], structuredContent=None)
            r = self.results.get(key)
            recs = [] if beh == "empty" or r is None else [{**x, "log": f"password=hunter2 {PLAIN}"} for x in r.records]
            return NS(isError=False, content=[], structuredContent={
                "records": recs, "metadata": {"grail": {"queryId": f"q-{key}"}}})
        finally:
            self.active -= 1


def _out(span: dict) -> dict:
    """LangChain tool run'ı çıktıyı {"output": ...} altına koyar."""
    return span["outputs"].get("output", span["outputs"])


def _setup(tmp_path, plan, *, retries=1):
    c = cfg()
    settings = Settings(data_dir=tmp_path, cfg=c)
    ws = windows()
    start, end = ws[1].start, ws[1].end
    gws = window_list(start, end, c["windows"]["baseline_minutes"])
    base, win, extra = SCENARIOS["row_lock"]()
    results = build(base, win, gws, **extra)
    dql_key = {d: k for k, d in build_queries(gws, c, list(CATALOG))}
    session = FakeSession(dql_key, results, plan)
    events: list[dict] = []

    @contextlib.asynccontextmanager
    async def sess(_server):
        yield session

    def factory():
        gw = McpGateway("http://mcp.test", "token-x", timeout_s=0.3, retries=retries, concurrency=4,
                        on_event=events.append)
        gw.client = NS(session=sess)
        return gw
    return settings, factory, start, end, events, dql_key, session


def test_tree_parallel_mcp_retry_and_no_leak(tmp_path):
    keys = list(CATALOG)
    plan = {keys[0]: ["timeout", "ok"], keys[1]: ["perm"], keys[2]: ["empty"]}
    settings, factory, start, end, events, dql_key, session = _setup(tmp_path, plan)
    cap, rid = Capture(), uuid.uuid4()
    out = run_analysis(settings, factory, start, end, llm=SecretLlm(), question=QUESTION, mode="manual",
                       callbacks=[cap.tracer], trace_run_id=rid, window_source="user",
                       trace_metadata={"trigger": "manual", "environment": "test", "investigation_id": str(rid)})
    assert out["report"]["id"] and session.peak > 1                    # MCP çağrıları gerçekten paralel

    roots = cap.roots()
    assert len(roots) == 1 and roots[0]["id"] == str(rid) and roots[0]["name"] == "airlab-manual"
    m = cap.meta(roots[0])
    assert m["trigger"] == "manual" and m["platform_run_id"] == m["thread_id"] == out["run_id"]
    assert m["mode"] == "manual" and m["environment"] == "test"
    runs = cap.runs()
    assert {str(r.get("trace_id")) for r in runs.values()} == {str(rid)}   # hepsi aynı kök trace'te

    kids = {r["name"]: r for r in cap.children(rid)}
    for n in NODES:                                                    # sabit adlar, her biri bir kez
        assert len([r for r in cap.children(rid) if r["name"] == n]) == 1, n
    plan_s = kids["plan"]["outputs"]["step_summary"]
    assert plan_s["window_source"] == "user" and plan_s["analysis_window"]["start"]

    # MCP: her mantıksal çağrı collect altında tek tool span'i; span id = mcp_call_id = platform olayındaki kimlik
    spans = [r for r in runs.values() if r.get("run_type") == "tool" and r["name"].startswith("mcp:")]
    collect_id = kids["collect"]["id"]
    in_collect = [s for s in spans if s["parent_run_id"] == collect_id]
    assert len(in_collect) == len(keys)
    extra_runs = [r for r in cap.children(rid) if r["name"] == "extra_evidence"]
    assert extra_runs and all(s["parent_run_id"] == extra_runs[0]["id"] for s in spans if s not in in_collect)
    xs = extra_runs[0]["outputs"]["step_summary"]                      # karar, neden ve iterasyon
    assert xs["iteration"] == 1 and xs["decision"] == "collect" and "row_lock" in xs["reason"]
    start_ids = {e["mcp_call_id"] for e in events if e["phase"] == "start"}
    assert {s["id"] for s in spans} == start_ids
    by_key = {s["inputs"]["query_key"]: s for s in in_collect}
    s0 = by_key[keys[0]]
    assert s0["inputs"]["connector"] == "dynatrace" and s0["inputs"]["tool"] == "execute-dql"
    assert s0["inputs"]["time_range"]["start"] and s0["inputs"]["mcp_call_id"] == s0["id"]
    o0 = _out(s0)
    assert [a["outcome"] for a in o0["attempts"]] == ["timeout", "success"]                  # retry aynı span'de
    assert o0["outcome"] == "success" and o0["evidence_id"].startswith("ev-") and o0["attempts_count"] == 2
    assert by_key[keys[1]]["error"].startswith("TraceSafeError: outcome=permission_denied")
    assert _out(by_key[keys[2]])["outcome"] == "empty"
    co = kids["collect"]["outputs"]["step_summary"]
    assert co["outcomes"]["permission_denied"] == 1 and co["outcomes"]["empty"] >= 1
    assert co["signals_requested"] == len(keys)

    an = kids["analyze"]["outputs"]["step_summary"]
    assert an["evidence_ids"] and "score" in an["risk"] and an["data_quality"][keys[1]] == "PERMISSION"
    assert "extra_decision" in kids["analyze"]["outputs"]
    ex = kids["explain"]["outputs"]["step_summary"]
    assert ex["llm_called"] and ex["model"] == "fake-rca" and ex["outcome"] == "ok" and ex["duration_ms"] >= 0
    llm_runs = [r for r in runs.values() if r.get("run_type") == "llm"]
    assert len(llm_runs) == 1 and llm_runs[0]["parent_run_id"] == kids["explain"]["id"]
    fin = kids["finalize"]["outputs"]["step_summary"]
    assert fin["report_id"] == out["run_id"] and fin["status"] in ("complete", "partial")

    # gizli değerler: allowlist dışı düz metin, redaksiyon kalıpları, ham DQL, soru, model çıktısı yok
    blob = cap.blob()
    for bad in (PLAIN, ANTHROPIC, "hunter2", "ali@example.com", "Checkin neden yavaş", "Kök neden row lock"):
        assert bad not in blob, bad
    for d in dql_key:
        assert d[:80] not in blob
    assert "_omitted" in json.dumps(roots[0]["inputs"])               # atılan alanların yalnız adları


def test_mcp_timeout_marks_span_and_partial_report(tmp_path):
    keys = list(CATALOG)
    settings, factory, start, end, events, _, _ = _setup(tmp_path, {keys[0]: ["timeout"]})
    cap, rid = Capture(), uuid.uuid4()
    out = run_analysis(settings, factory, start, end, use_llm=False, mode="scheduled", callbacks=[cap.tracer],
                       trace_run_id=rid, window_source="scheduler", trace_metadata={"trigger": "scheduled"})
    assert out["report"]["status"] == "partial"                        # agent kısmi raporla devam etti
    root = cap.roots()[0]
    assert root["id"] == str(rid) and not root.get("error") and cap.meta(root)["trigger"] == "scheduled"
    kids = {r["name"]: r for r in cap.children(rid)}
    s0 = next(r for r in cap.runs().values() if r["name"] == f"mcp:{keys[0]}")
    assert s0["error"].startswith("TraceSafeError: outcome=timeout attempts=2")              # hatalı çağrı görünür
    assert kids["collect"]["outputs"]["step_summary"]["mcp_failure"] == "timeout"
    fin = kids["finalize"]["outputs"]["step_summary"]
    assert fin["status"] == "partial" and "mcp_timeout" in fin["partial_reasons"]           # run durumu ayrı alan
    assert kids["plan"]["outputs"]["step_summary"]["window_source"] == "scheduler"
    ends = [e for e in events if e["phase"] == "end" and e["key"] == keys[0]]
    assert ends and ends[0]["status"] == "TIMEOUT" and ends[0]["outcome"] == "timeout"


def test_exception_detail_is_not_sent(tmp_path):
    settings = Settings(data_dir=tmp_path, cfg=cfg())
    ws = windows()
    cap, rid = Capture(), uuid.uuid4()

    def boom():
        raise RuntimeError(f"bağlantı hatası password={PLAIN} {ANTHROPIC}")
    with pytest.raises(RuntimeError):
        run_analysis(settings, boom, ws[1].start, ws[1].end, use_llm=False, callbacks=[cap.tracer], trace_run_id=rid)
    root = cap.roots()[0]
    assert root["error"].startswith("RuntimeError") and "ayrıntı platform kaydında" in root["error"]
    assert PLAIN not in cap.blob() and "sk-ant" not in cap.blob() and "Traceback" not in cap.blob()


def test_output_preserved_when_tracing_off_or_unreachable(tmp_path):
    from airlab_agent.tracing import make_client, make_tracer
    settings, factory, start, end, *_ = _setup(tmp_path, {})
    base = run_analysis(settings, factory, start, end, use_llm=False, callbacks=[])
    cap = Capture()
    off = without_tracing(run_analysis, settings, factory, start, end, use_llm=False, callbacks=[cap.tracer])
    assert cap.runs() == {}                                            # tracing kapalı: hiçbir şey gönderilmedi
    dead = make_tracer("x", make_client("lsv2_pt_" + "1" * 32, api_url="http://127.0.0.1:9"))
    t0 = time.monotonic()
    down = run_analysis(settings, factory, start, end, use_llm=False, callbacks=[dead])
    assert time.monotonic() - t0 < 30
    for r in (off, down):
        assert r["report"]["risk"] == base["report"]["risk"] and r["report"]["status"] == base["report"]["status"]


def test_trace_safe_allowlist_shapes():
    llm_out = {"generations": [[{"text": PLAIN, "message": {"lc": 1, "type": "constructor", "kwargs": {
        "content": PLAIN, "usage_metadata": {"input_tokens": 10, "output_tokens": 3, "total_tokens": 13},
        "response_metadata": {"model_name": "claude-opus-5-5", "stop_reason": "end_turn"}}}}]],
        "llm_output": {"model_name": "claude-opus-5-5"}}
    s = trace_safe(llm_out)
    k = s["generations"][0][0]["message"]["kwargs"]
    assert k["usage_metadata"]["input_tokens"] == 10 and k["response_metadata"]["stop_reason"] == "end_turn"
    assert PLAIN not in json.dumps(s)
    assert trace_safe({"raw": {}, "parsed": None, "parsing_error": f"bad {PLAIN}"})["validation"] == "failed"
    assert trace_safe({"raw": {}, "parsed": {"intent": PLAIN}, "parsing_error": None})["validation"] == "ok"
    st = trace_safe({"question": PLAIN, "results": {"x": {"dql": "fetch logs", "records": [{"a": PLAIN}]}},
                     "status": "running", "step_summary": {"note": f"token={ANTHROPIC}"}})
    assert st["status"] == "running" and st["_omitted"] == ["question", "results"]
    assert ANTHROPIC not in json.dumps(st) and PLAIN not in json.dumps(st)
