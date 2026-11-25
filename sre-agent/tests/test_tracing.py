"""Trace hiyerarşisi: alt çağrılar üst adımın trace'ine yerleşir (ayrı kök trace açılmaz); DQL tool span'i de öyle."""
from __future__ import annotations

from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.runnables import RunnableLambda

from airlab_agent.tracing import ToolSpan, child_config, parent_config


class Cap(BaseCallbackHandler):
    def __init__(self):
        self.runs: list[tuple[str, str, object, object]] = []      # (tür, ad, run_id, parent_run_id)

    def on_chain_start(self, serialized, inputs, *, run_id, parent_run_id=None, **kw):
        self.runs.append(("chain", kw.get("name") or "", run_id, parent_run_id))

    def on_tool_start(self, serialized, input_str, *, run_id, parent_run_id=None, **kw):
        self.runs.append(("tool", kw.get("name") or serialized.get("name", ""), run_id, parent_run_id))


def test_child_inherits_parent_trace_and_tool_span_nests():
    cap, other = Cap(), Cap()

    def inner(x):
        RunnableLambda(lambda y: y).invoke(x, config=child_config([other], "llm_call"))
        span = ToolSpan(parent_config(), "dql:db_sessions", {"signal": "db_sessions", "dql": "fetch x"})
        span.end({"status": "OK", "records": 3})
        return x

    RunnableLambda(inner).invoke(1, config={"callbacks": [cap], "run_name": "chat-turn"})
    root = next(r for r in cap.runs if r[1] == "chat-turn")
    assert root[3] is None
    child = next(r for r in cap.runs if r[1] == "llm_call")
    tool = next(r for r in cap.runs if r[0] == "tool")
    assert child[3] == root[2] and tool[3] == root[2] and tool[1] == "dql:db_sessions"
    assert other.runs == []                                   # ayrı kök trace açılmadı


def test_without_parent_opens_own_root_and_toolspan_is_noop():
    cap = Cap()
    cfg = child_config([cap], "explain_findings")
    assert cfg["callbacks"] == [cap]
    RunnableLambda(lambda y: y).invoke(1, config=cfg)
    assert cap.runs[0][1] == "explain_findings" and cap.runs[0][3] is None
    ToolSpan(None, "dql:x", {"dql": "q"}).end({"status": "OK"})   # tracing yok → hata yok


def test_run_analysis_nests_under_parent_with_given_run_id(tmp_path):
    import sys
    import uuid
    sys.path.insert(0, "tests")
    from synth import FakeGateway, build, cfg, windows
    from test_scenarios import SCENARIOS

    from airlab_agent.config import Settings
    from airlab_agent.graph import run_analysis
    settings = Settings(data_dir=tmp_path, cfg=cfg())
    ws = windows()
    base, win, extra = SCENARIOS["row_lock"]()
    gw = FakeGateway(build(base, win, ws, **extra))
    cap, rid = Cap(), uuid.uuid4()

    def turn(_):
        return run_analysis(settings, lambda: gw, ws[1].start, ws[1].end, use_llm=False, fixture=True,
                            trace_run_id=rid, trace_metadata={"investigation_id": str(rid)})

    RunnableLambda(turn).invoke(0, config={"callbacks": [cap], "run_name": "chat-turn"})
    root = next(r for r in cap.runs if r[1] == "chat-turn")
    analysis = next(r for r in cap.runs if r[1] == "airlab-analyze")
    assert analysis[2] == rid and analysis[3] == root[2]
    assert any(r[3] == rid for r in cap.runs)                     # graf düğümleri analizin altında
