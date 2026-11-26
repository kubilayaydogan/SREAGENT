"""Agent Middleware çekirdeği: gerçek çağrı yollarında (MCP gateway, LLM istemcisi, streaming) davranış testleri."""
from __future__ import annotations

import asyncio
import contextvars
import threading
from types import SimpleNamespace as NS

import pytest

from airlab_agent import middleware as mw
from airlab_agent.llm import RUN_BUDGET, LlmBudgetExceeded, RunBudget
from airlab_agent.mcp_gateway import McpGateway

# Fixture parçalı yazılır: kaynakta tam secret kalıbı bulunmaz (gitleaks), çalışma anında birleşir.
SECRET = "Bearer " + "testonly" + "abcdefgh12345678"
LEAK = "abcdefgh12345678"


class _Session:
    def __init__(self):
        self.calls = 0

    async def call_tool(self, name, args):
        self.calls += 1
        await asyncio.sleep(0.01)
        return NS(content=[NS(text="")], structuredContent={"records": [{"a": 1}]}, isError=False)


def _gw(**kw):
    return McpGateway("https://abc.apps.dynatrace.com/mcp", "tok", retries=0, **kw)


def _run(cfg_over: dict | None = None, **kw) -> tuple[mw.RunMw, list[dict]]:
    recs: list[dict] = []
    cfg = mw.default_config()
    for n, c in (cfg_over or {}).items():
        cfg[n] = {**cfg[n], **c}
    return mw.RunMw(run_id=kw.pop("run_id", "run-1"), config=cfg, sink=recs.append, **kw), recs


def _in(run: mw.RunMw, fn, *a):
    ctx = contextvars.copy_context()
    ctx.run(mw.RUN.set, run)
    return ctx.run(fn, *a)


def _tool_call(gw, sess, key="k", dql="fetch logs | limit 1"):
    return asyncio.run(gw._one_inner(sess, key, dql))


# ---------------------------------------------------------------------------------------------- engelleme
def test_blocked_tool_is_never_sent_and_matches_old_result():
    run, recs = _run({"tool_allowlist": {"params": {"allowed_connectors": ["dynatrace"], "allowed_tools": ["x"]}}})
    gw, sess, events = _gw(), _Session(), []
    gw.on_event = events.append
    r = _in(run, _tool_call, gw, sess)
    assert sess.calls == 0 and gw.queries_used == 0
    assert r.status == "ERROR" and r.outcome == "rejected" and "tool_not_allowed" in r.error
    assert events[-1]["sent"] is False
    blk = [x for x in recs if x["middleware"] == "tool_allowlist"]
    assert blk[0]["decision"] == "block" and blk[0]["outcome"] == "blocked"
    # engelleyen katmandan sonraki (iç) wrapper'lar hiç çalışmaz
    assert not [x for x in recs if x["middleware"] in ("tool_param_validation", "call_limit", "retry_observer")]
    env = next(x for x in recs if x["entry_type"] == "call")
    assert env["outcome"] == "blocked" and env["wrapped_ms"] is None


def test_invalid_dql_and_budget_blocked_by_middleware_without_send():
    run, recs = _run()
    gw, sess = _gw(max_queries=1), _Session()
    r1 = _in(run, _tool_call, gw, sess, "bad", "delete bucket x")
    r2 = _in(run, _tool_call, gw, sess, "ok", "fetch logs | limit 1")
    r3 = _in(run, _tool_call, gw, sess, "over", "fetch logs | limit 2")
    assert (r1.status, r1.outcome) == ("ERROR", "rejected") and r1.error.startswith("DQL reddedildi")
    assert r2.status == "OK" and r3.status == "SKIPPED_BUDGET" and sess.calls == 1
    codes = [x["error_code"] for x in recs if x["decision"] == "block" and x["entry_type"] == "middleware"]
    assert codes == ["dql_rejected", "query_budget"]


def test_works_without_run_context_like_before():
    gw, sess = _gw(), _Session()
    r = _tool_call(gw, sess)                       # RUN yok: kod varsayılanı, kayıt yok
    assert r.status == "OK" and sess.calls == 1
    assert _tool_call(gw, sess, "x", "delete x").outcome == "rejected" and sess.calls == 1


# ---------------------------------------------------------------------------------------------- sıra ve iç içelik
class _Probe(mw.Middleware):
    kind, description, title = "custom", "test", "probe"
    hooks = ("before_model", "wrap_model_call", "after_model")

    def __init__(self, name, order, log):
        self.name, self.default_order, self.log = name, order, log

    def pre(self, hook, call, p, run):
        self.log.append(f"{self.name}:{hook}:pre")

    def post(self, hook, call, p, run):
        self.log.append(f"{self.name}:{hook}:post")


def test_ordering_before_asc_wrap_nested_after_reverse(monkeypatch):
    log: list[str] = []
    reg = {"a": _Probe("a", 1, log), "b": _Probe("b", 2, log)}
    monkeypatch.setattr(mw, "REGISTRY", reg)
    monkeypatch.setattr(mw, "_DEFAULT_RUN_CFG", mw.default_config())
    run, recs = _run()
    out = _in(run, mw.run_model_sync, mw.Call(kind="model", target="m", task="t"), lambda req: log.append("CALL") or 7)
    assert out == 7
    assert log == ["a:before_model:pre", "b:before_model:pre", "a:wrap_model_call:pre", "b:wrap_model_call:pre", "CALL",
                   "b:wrap_model_call:post", "a:wrap_model_call:post", "b:after_model:post", "a:after_model:post"]
    wraps = {x["middleware"]: x for x in recs if x["hook"] == "wrap_model_call"}
    env = next(x for x in recs if x["entry_type"] == "call")
    assert wraps["a"]["parent_execution_id"] == env["execution_id"] and wraps["a"]["depth"] == 1
    assert wraps["b"]["parent_execution_id"] == wraps["a"]["execution_id"] and wraps["b"]["depth"] == 2
    assert wraps["a"]["sequence"] < wraps["b"]["sequence"]
    assert wraps["a"]["wrapped_ms"] >= wraps["b"]["wrapped_ms"]          # dıştaki iç çağrıyı kapsar


def test_order_from_config_overrides_default(monkeypatch):
    log: list[str] = []
    monkeypatch.setattr(mw, "REGISTRY", {"a": _Probe("a", 1, log), "b": _Probe("b", 2, log)})
    monkeypatch.setattr(mw, "_DEFAULT_RUN_CFG", mw.default_config())
    run, _ = _run({"a": {"order": 5}})
    _in(run, mw.run_model_sync, mw.Call(kind="model", target="m"), lambda req: None)
    assert log[0].startswith("b:before_model")


# ---------------------------------------------------------------------------------------------- gizlilik
def test_sensitive_data_masked_before_model_and_in_records():
    run, recs = _run(settings={"record_executions": True, "capture_payloads": True, "max_payload_bytes": 16000})
    seen = {}

    def model(req):
        seen["msgs"] = req["messages"]
        return "ok"
    msgs = [("system", "sistem"), ("human", f"log satırı: Authorization: {SECRET} ve ayse@ornek.com")]
    _in(run, mw.run_model_sync, mw.Call(kind="model", target="m", task="t", request={"messages": msgs}), model)
    assert LEAK not in repr(seen["msgs"]) and "ayse@ornek.com" not in repr(seen["msgs"])
    assert seen["msgs"][0] == ("system", "sistem")
    red = next(x for x in recs if x["middleware"] == "redaction")
    assert red["decision"] == "modify" and red["changed_fields"] == ["messages[1]"]
    blob = repr(recs)
    assert LEAK not in blob and "ayse@ornek.com" not in blob


def test_payloads_not_captured_by_default_and_truncated_when_large():
    run, recs = _run()
    _in(run, mw.run_model_sync, mw.Call(kind="model", target="m", request={"messages": [("human", "x")]}), lambda r: 1)
    assert all(x["before"] is None and x["after"] is None for x in recs)
    run2, recs2 = _run(settings={"capture_payloads": True, "max_payload_bytes": 1000, "record_executions": True})
    big = [("human", "y" * 5000)]
    _in(run2, mw.run_model_sync, mw.Call(kind="model", target="m", request={"messages": big}), lambda r: 1)
    assert any(x["truncated"] for x in recs2)


def test_dql_not_stored_raw_in_tool_records():
    run, recs = _run(settings={"capture_payloads": True, "max_payload_bytes": 16000, "record_executions": True})
    dql = 'fetch logs | filter content == "PLAIN-SECRET-VALUE-42" | limit 5'
    _in(run, _tool_call, _gw(), _Session(), "logs", dql)
    assert "PLAIN-SECRET-VALUE-42" not in repr(recs)


# ---------------------------------------------------------------------------------------------- izolasyon / snapshot
def test_parallel_runs_are_isolated():
    runs = [_run({"tool_allowlist": {"version": i + 1}}, run_id=f"r{i}") for i in range(4)]
    barrier = threading.Barrier(4)

    def work(run):
        barrier.wait()
        gw = _gw(concurrency=4)
        sess = _Session()

        async def many():
            return await asyncio.gather(*(gw._one_inner(sess, f"k{j}", "fetch logs | limit 1") for j in range(5)))
        _in(run, asyncio.run, many())
    ts = [threading.Thread(target=work, args=(r,)) for r, _ in runs]
    [t.start() for t in ts]
    [t.join() for t in ts]
    for i, (run, recs) in enumerate(runs):
        assert recs and all(x["run_id"] == f"r{i}" for x in recs)
        assert {x["middleware_version"] for x in recs if x["middleware"] == "tool_allowlist"} == {i + 1}
        assert len([x for x in recs if x["entry_type"] == "call"]) == 5
        seqs = [x["sequence"] for x in recs]
        assert len(seqs) == len(set(seqs))                                   # run içi sıra numarası tekil


def test_config_snapshot_fixed_for_the_run():
    cfg = mw.default_config()
    run, recs = _run()
    run.config = {n: dict(c) for n, c in cfg.items()}
    cfg["tool_allowlist"]["version"] = 99                     # run başladıktan sonra "yeni sürüm"
    _in(run, _tool_call, _gw(), _Session())
    assert {x["middleware_version"] for x in recs if x["middleware"] == "tool_allowlist"} == {0}


def test_mandatory_middleware_cannot_be_disabled():
    with pytest.raises(ValueError):
        mw.validate_config("tool_allowlist", {"enabled": False})
    with pytest.raises(ValueError):
        mw.validate_config("redaction", {"enabled": True, "scope": {"kinds": ["tool"]}})
    with pytest.raises(ValueError):
        mw.validate_config("tool_param_validation", {"params": {"allowed_commands": ["delete"]}})
    with pytest.raises(ValueError):
        mw.check_orders({"a": {"order": 5}, "b": {"order": 5}})
    # bozuk/elle değiştirilmiş snapshot'ta bile zorunlu middleware çalışır
    run, recs = _run({"tool_allowlist": {"enabled": False, "params": {"allowed_tools": ["x"]}}})
    sess = _Session()
    _in(run, _tool_call, _gw(), sess)
    assert sess.calls == 0


# ---------------------------------------------------------------------------------------------- hata politikaları
def test_telemetry_failure_does_not_affect_call():
    def bad_sink(_):
        raise RuntimeError("db down")
    run = mw.RunMw(run_id="r", sink=bad_sink)
    sess = _Session()
    r = _in(run, _tool_call, _gw(), sess)
    assert r.status == "OK" and sess.calls == 1 and run.lost > 0


def test_security_middleware_error_blocks_but_logging_error_continues(monkeypatch):
    def boom(self, *a):
        raise RuntimeError("kural motoru hatası")
    monkeypatch.setattr(mw.ToolAllowlist, "pre", boom)
    run, recs = _run()
    sess = _Session()
    r = _in(run, _tool_call, _gw(), sess)
    assert sess.calls == 0 and r.outcome == "rejected"
    x = next(x for x in recs if x["middleware"] == "tool_allowlist")
    assert (x["decision"], x["error_code"], x["result"]) == ("block", "middleware_error", "error")
    monkeypatch.undo()
    monkeypatch.setattr(mw.CallLogging, "pre", boom)
    run2, recs2 = _run()
    sess2 = _Session()
    assert _in(run2, _tool_call, _gw(), sess2).status == "OK" and sess2.calls == 1
    x2 = next(x for x in recs2 if x["middleware"] == "call_logging")
    assert x2["result"] == "error" and x2["decision"] == "not_applicable"


# ---------------------------------------------------------------------------------------------- model: sync / async / stream
def test_llm_budget_block_raises_same_exception_and_skips_call():
    run, recs = _run()
    called = []

    def go():
        RUN_BUDGET.set(RunBudget(max_calls=0, max_tokens=10))
        return mw.run_model_sync(mw.Call(kind="model", target="m", task="explain_findings"),
                                 lambda r: called.append(1))
    with pytest.raises(LlmBudgetExceeded):
        _in(run, go)
    assert not called
    assert next(x for x in recs if x["middleware"] == "call_limit")["error_code"] == "llm_budget"


def test_async_model_call_and_error_recorded():
    run, recs = _run()

    async def ok(req):
        return 3

    async def fail(req):
        raise TimeoutError("yavaş")

    async def both():
        assert await mw.run_model_async(mw.Call(kind="model", target="m"), ok) == 3
        with pytest.raises(TimeoutError):
            await mw.run_model_async(mw.Call(kind="model", target="m"), fail)
    _in(run, asyncio.run, both())
    envs = [x for x in recs if x["entry_type"] == "call"]
    assert [e["outcome"] for e in envs] == ["completed", "error"] and envs[1]["error_code"] == "TimeoutError"


def test_streaming_chunks_pass_through_unbuffered():
    run, recs = _run()
    gate = asyncio.Event

    async def scenario():
        g = gate()

        async def source(req):
            yield "a"
            await asyncio.wait_for(g.wait(), 2)           # tüketici ilk chunk'ı almadan ilerlemez (buffer olsa deadlock olur)
            yield "b"
            yield "c"
        got = []
        async for ch in mw.stream_model(mw.Call(kind="model", target="m", task="answer"), source):
            got.append(ch)
            g.set()
        return got
    assert _in(run, asyncio.run, scenario()) == ["a", "b", "c"]
    env = next(x for x in recs if x["entry_type"] == "call")
    assert env["extra"]["stream"]["chunks"] == 3 and env["extra"]["stream"]["completed"] is True


# ---------------------------------------------------------------------------------------------- lifecycle / dry-run
def test_after_agent_not_used_as_finally():
    run, recs = _run()
    mw.agent_start(run, graph="g")
    mw.agent_end(run, graph="g", status="failed", error="x")
    assert not [x for x in recs if x["hook"] == "after_agent"]
    root = next(x for x in recs if x["entry_type"] == "agent")
    assert root["outcome"] == "failed" and root["execution_id"] == run.root_id and root["sequence"] == 0
    run2, recs2 = _run()
    mw.agent_end(run2, graph="g", status="completed")
    assert [x for x in recs2 if x["hook"] == "after_agent"]


def test_dry_run_has_no_side_effects():
    out = mw.dry_run("tool_param_validation", "wrap_tool_call", {"target": "execute-dql", "dql": "delete x"}, None, None)
    assert out["decision"] == "block" and out["code"] == "dql_rejected"
    assert mw.dry_run("timing", "wrap_model_call", {}, None, None)["supported"] is False
    assert mw.dry_run("redaction", "wrap_tool_call", {}, None, None)["supported"] is False
    bad = mw.dry_run("timing", "wrap_tool_call", {}, {"slow_call_ms": 1}, None)
    assert bad.get("param_errors")
    lim = mw.dry_run("call_limit", "wrap_tool_call", {"target": "execute-dql"}, None,
                     {"queries_used": 3, "max_queries": 3})
    assert lim["decision"] == "block"
    red = mw.dry_run("redaction", "before_model", {"request": {"messages": [("human", SECRET)]}}, None, None)
    assert red["decision"] == "modify" and LEAK not in repr(red["after"])
