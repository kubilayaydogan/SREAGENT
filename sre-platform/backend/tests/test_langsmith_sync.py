"""LangSmith → sre.llm_runs senkronu ve LLM izleme özeti."""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from sqlalchemy import func, select

from conftest import run
from sre_platform.db.models import KeyValue, LlmRun
from sre_platform.services import langsmith_sync as ls
from sre_platform.services import llm_observability as obs

NOW = datetime.now(timezone.utc).replace(microsecond=0)
INV = str(uuid.UUID(int=900))


def U(n: int) -> str:
    return str(uuid.UUID(int=n))


def R(n, name, run_type, *, trace, start_min, lat=None, status="success", parents=(), md=None, root=False,
      pt=None, ct=None, cost=None, err=None, ftt=None):
    st = NOW - timedelta(minutes=start_min)
    return SimpleNamespace(id=U(n), name=name, run_type=run_type, status=status, start_time=st,
                           end_time=st + timedelta(seconds=lat) if lat else None, latency_seconds=lat,
                           first_token_time=st + timedelta(seconds=ftt) if ftt else None, error_preview=err,
                           metadata=md or {}, parent_run_ids=[U(p) for p in parents], trace_id=U(trace),
                           is_root=root, prompt_tokens=pt, completion_tokens=ct, total_cost=cost)


RUNS = [
    # sohbet turu: route_question (ChatAnthropic, atası route_question) + answer
    R(1, "chat-turn", "chain", trace=1, start_min=30, lat=12, root=True,
      md={"conversation_id": U(500), "model": "claude-opus-5-5"}),
    R(2, "route_question", "chain", trace=1, start_min=30, parents=(1,)),
    R(3, "RunnableWithFallbacks", "chain", trace=1, start_min=30, parents=(1, 2)),
    R(4, "ChatAnthropic", "llm", trace=1, start_min=30, lat=2, parents=(1, 2, 3), pt=1000, ct=10, cost=0.02,
      md={"ls_model_name": "claude-opus-5-5"}),
    R(5, "answer", "llm", trace=1, start_min=29, lat=8, ftt=1.5, parents=(1,), pt=3000, ct=500, cost=0.1,
      md={"ls_model_name": "claude-opus-5-5"}),
    # zamanlanmış analiz: explain_findings hata
    R(10, "airlab-scheduled", "chain", trace=10, start_min=120, lat=30, root=True, status="error",
      md={"investigation_id": INV, "trigger": "scheduled"},
      err="Timeout sk-ant-api03-SECRETSECRETSECRET"),
    R(11, "explain_findings", "llm", trace=10, start_min=119, lat=25, status="error", parents=(10,), pt=7000, ct=0,
      cost=0.01, md={"ls_model_name": "claude-sonnet-5-5", "investigation_id": INV}, err="overloaded"),
    # tek başına LLM çağrısı (RCA raporu): hem kök hem llm sorgusunda döner
    R(30, "explain_findings", "llm", trace=30, start_min=60, lat=5, root=True, pt=500, ct=100, cost=0.005,
      md={"ls_model_name": "claude-opus-5-5"}),
    # hâlâ süren
    R(20, "chat-turn", "chain", trace=20, start_min=2, root=True, status="pending", md={"conversation_id": U(501)}),
]


class FakeRuns:
    def __init__(self, runs):
        self.runs, self.calls = runs, []

    def query(self, *, project_ids, selects, page_size, ids=None, is_root=None, run_type=None, min_start_time=None):
        self.calls.append({"ids": ids, "is_root": is_root, "run_type": run_type, "since": min_start_time})

        async def gen():
            for r in self.runs:
                if ids is not None and r.id not in ids:
                    continue
                if is_root is not None and r.is_root != is_root:
                    continue
                if run_type and r.run_type != run_type:
                    continue
                if min_start_time and r.start_time < min_start_time:
                    continue
                yield r
        return gen()


def _rt(settings, sm, runs):
    client = SimpleNamespace(runs=FakeRuns(runs), read_project=lambda project_name: SimpleNamespace(id=U(7)))
    tracer = SimpleNamespace(client=client, project_name="airlab-test")
    return SimpleNamespace(ps=settings, sm=sm, callbacks=[tracer]), client


def test_sync_labels_upserts_and_summarizes(settings):
    from sre_platform.db.session import make_engine, make_sessionmaker
    ls._project_ids.clear()

    async def go():
        eng = make_engine(settings, "pytest")
        sm = make_sessionmaker(eng)
        try:
            rt, client = _rt(settings, sm, RUNS)
            r1 = await ls.sync(rt)
            await ls.sync(rt)                                           # idempotent
            async with sm() as s:
                rows = {str(r.id): r for r in (await s.scalars(select(LlmRun))).all()}
                n = await s.scalar(select(func.count()).select_from(LlmRun))
                state = (await s.get(KeyValue, ls.KEY)).value
                summ = await obs.summary(s, "24h")
            off = await ls.sync(SimpleNamespace(ps=settings, sm=sm, callbacks=[]))
            return r1, rows, n, state, summ, off, client
        finally:
            await eng.dispose()

    r1, rows, n, state, summ, off, client = run(go)
    assert (r1["traces"], r1["llm_calls"], r1["pending"]) == (3, 4, 1) and n == 7
    assert rows[U(30)].kind == "llm"
    assert off == {"skipped": "LangSmith tracing kapalı"}
    # etiket: genel adlı LLM çağrısı en yakın anlamlı atasının adını alır
    assert rows[U(4)].name == "route_question" and rows[U(4)].model == "claude-opus-5-5"
    assert rows[U(5)].first_token_ms == 1500 and rows[U(5)].latency_ms == 8000
    assert rows[U(1)].trigger == "chat" and str(rows[U(1)].conversation_id) == U(500)
    assert rows[U(10)].trigger == "scheduled" and str(rows[U(10)].investigation_id) == INV
    assert "SECRETSECRET" not in (rows[U(10)].error or "")                 # hata metni redakte
    assert rows[U(20)].status == "pending"
    # imleç: süren run'ın başlangıcından ileri gitmez
    assert datetime.fromisoformat(state["since"]) <= NOW - timedelta(minutes=2)
    assert client.runs.calls[-1]["since"] >= NOW - timedelta(days=settings.langsmith_backfill_days, minutes=1)

    t = summ["totals"]
    assert (t["calls"], t["errors"], t["traces"], t["trace_errors"]) == (4, 1, 4, 1)
    assert t["input_tokens"] == 11500 and t["output_tokens"] == 610 and abs(t["cost_usd"] - 0.135) < 1e-9
    assert abs(t["error_rate"] - 1 / 4) < 1e-9 and t["p95_first_token_ms"] == 1500
    assert {m["model"]: m["calls"] for m in summ["by_model"]} == {"claude-opus-5-5": 3, "claude-sonnet-5-5": 1}
    assert {c["name"] for c in summ["by_call"]} == {"route_question", "answer", "explain_findings"}
    trig = {x["trigger"]: x for x in summ["by_trigger"]}
    assert trig["chat"]["traces"] == 2 and abs(trig["chat"]["cost_usd"] - 0.12) < 1e-9
    assert trig["scheduled"]["errors"] == 1
    assert summ["top_cost"][0]["id"] == U(1) and summ["top_cost"][0]["llm_calls"] == 2
    assert summ["errors"][0]["id"] == U(10) and summ["errors"][0]["model"] == "claude-sonnet-5-5"
    assert summ["errors"][0]["id"] == U(10) and summ["llm_errors"][0]["name"] == "explain_findings"
    assert sum(b["calls"] for b in summ["series"]) == 4
    assert abs(trig["rca"]["cost_usd"] - 0.005) < 1e-9                      # tek başına explain_findings = RCA raporu
    assert summ["sync"]["last_ok_at"] and summ["sync"]["last_error"] is None


def test_sync_failure_keeps_cursor_and_records_error(settings):
    from sre_platform.db.session import make_engine, make_sessionmaker
    ls._project_ids.clear()

    async def go():
        eng = make_engine(settings, "pytest")
        sm = make_sessionmaker(eng)
        try:
            rt, client = _rt(settings, sm, RUNS)
            await ls.sync(rt)
            async with sm() as s:
                before = (await s.get(KeyValue, ls.KEY)).value["since"]

            def boom(**kw):
                raise ConnectionError("langsmith down")
            client.runs.query = boom
            try:
                await ls.sync(rt)
                raised = False
            except RuntimeError:
                raised = True
            async with sm() as s:
                after = (await s.get(KeyValue, ls.KEY)).value
            return before, after, raised
        finally:
            await eng.dispose()

    before, after, raised = run(go)
    assert raised and after["since"] == before and "langsmith down" in after["last_error"]
