"""Faz 4 — FastAPI uçları uçtan uca (gerçek PostgreSQL, sahte MCP/Claude), SSE akışı ve telemetri hesapları."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import httpx
import pytest


from airlab_agent.mcp_gateway import QueryResult  # noqa: E402
from conftest import run  # noqa: E402
from synth import cfg, windows  # noqa: E402
from test_agent_memory import FakePort, _agent_settings, gw_factory  # noqa: E402

ADMIN_PW = "admin-parola-12345"
WS = windows()


@pytest.fixture
def api_settings(db_url, monkeypatch, lg_ready):
    monkeypatch.setenv("SRE_DATABASE_URL", db_url)
    monkeypatch.setenv("SRE_ADMIN_PASSWORD", ADMIN_PW)
    from sre_platform.core.config import load_platform_settings
    return load_platform_settings()


@pytest.fixture
def lg_ready(db_url):
    from sre_platform.db import migrate
    migrate.setup_langgraph(db_url.replace("postgresql+psycopg://", "postgresql://"))
    return True


def with_client(settings, tmp_path, fn, *, gw=None, llm=None):
    from sre_platform.agent.runtime import create_runtime
    from sre_platform.api.app import create_app

    async def factory(ps):
        return await create_runtime(ps, agent=_agent_settings(tmp_path), gateway_factory=gw or gw_factory(), llm=llm)

    app = create_app(settings, runtime_factory=factory)

    async def go():
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t",
                                         timeout=120) as c:
                return await fn(c, app)
    return run(go)


async def login(c, user="admin", pw=ADMIN_PW) -> dict:
    r = await c.post("/api/auth/login", json={"username": user, "password": pw})
    assert r.status_code == 200, r.text
    return {"Authorization": "Bearer " + r.json()["access_token"]}


async def mkuser(c, h, name, role):
    r = await c.post("/api/users", headers=h, json={"username": name, "display_name": name, "role": role,
                                                    "password": f"{name}-parola-123"})
    assert r.status_code == 201, r.text
    return await login(c, name, f"{name}-parola-123")


def sse(text: str) -> list[dict]:
    out = []
    for block in text.replace("\r\n", "\n").split("\n\n"):
        data = [ln[5:].strip() for ln in block.split("\n") if ln.startswith("data:")]
        if data and data[0]:
            out.append(json.loads("\n".join(data)))
    return out


TR = {"start": WS[1].start.isoformat(), "end": WS[1].end.isoformat()}


# ---------------------------------------------------------------- temel
def test_health_auth_rbac(api_settings, tmp_path):
    async def fn(c, app):
        assert (await c.get("/api/health")).json() == {"status": "ok"}
        assert (await c.get("/api/ready")).json()["status"] == "ready"
        r = await c.get("/api/auth/me")
        assert r.status_code == 401 and r.json()["error"] == "unauthorized" and r.json()["correlation_id"]
        h = await login(c)
        me = (await c.get("/api/auth/me", headers=h)).json()
        assert me["role"] == "admin" and "password_hash" not in me
        v = await mkuser(c, h, "izleyici", "viewer")
        r = await c.post("/api/agent/chat", headers=v, json={"message": "test"})
        assert r.status_code == 403 and r.json()["error"] == "forbidden"
        assert (await c.post("/api/incidents", headers=v, json={"title": "x x x"})).status_code == 403
        assert (await c.get("/api/users", headers=v)).status_code == 403
        assert (await c.put("/api/system/llm-model", headers=v, json={"model": "claude-sonnet-5-5"})).status_code == 403
        assert (await c.get("/api/system/llm-model", headers=v)).status_code == 200      # okuma herkese açık
        assert (await c.get("/api/observability/llm", headers=v)).status_code == 403      # LLM izleme: sre/admin
        o = await c.get("/api/observability/llm?range=7d", headers=h)
        assert o.status_code == 200 and o.json()["totals"]["calls"] == 0 and o.json()["totals"]["error_rate"] is None
        assert (await c.get("/api/observability/llm?range=1y", headers=h)).status_code == 422
        for _ in range(5):
            await c.post("/api/auth/login", json={"username": "izleyici", "password": "yanlis-parola"})
        assert (await c.post("/api/auth/login", json={"username": "izleyici",
                                                      "password": "izleyici-parola-123"})).status_code == 429
        r = await c.get("/api/health", headers={"X-Request-ID": "test-cid-123456"})
        assert r.headers["X-Correlation-ID"] == "test-cid-123456"
        info = (await c.get("/api/system/info", headers=h)).json()
        assert info["llm_enabled"] is False and "token" not in json.dumps(info).lower()
        apps = (await c.get("/api/applications?kind=business", headers=h)).json()
        assert [a["id"] for a in apps] == ["baggage-service", "checkin-service", "flightops-service",
                                           "reservation-service"]
    with_client(api_settings, tmp_path, fn)


def test_validation_errors_are_structured(api_settings, tmp_path):
    async def fn(c, app):
        h = await login(c)
        r = await c.post("/api/agent/chat", headers=h, json={"message": "x", "time_range": {"minutes": 0}})
        assert r.status_code == 422 and r.json()["error"] == "validation_error" and r.json()["details"]
        r = await c.post("/api/agent/chat", headers=h, json={"message": "x", "bilinmeyen": 1})
        assert r.status_code == 422
        r = await c.get("/api/investigations?sort=password_hash", headers=h)
        assert r.status_code == 422 and "sıralama" in r.json()["message"]
        r = await c.get("/api/investigations?status=YOK", headers=h)
        assert r.status_code == 422
        r = await c.get("/api/applications/yok-servis/health", headers=h)
        assert r.status_code == 404
    with_client(api_settings, tmp_path, fn)


# ---------------------------------------------------------------- sohbet + SSE
def test_chat_stream_and_records(api_settings, tmp_path):
    async def fn(c, app):
        h = await login(c)
        sre = await mkuser(c, h, "nobetci", "sre")
        r = await c.post("/api/agent/chat", headers=sre, json={"message": "Checkin neden yavaşladı?",
                                                               "time_range": TR})
        assert r.status_code == 202, r.text
        run_ = r.json()
        st = await c.get(run_["stream_url"], headers=sre)
        assert st.status_code == 200 and st.headers["content-type"].startswith("text/event-stream")
        evs = sse(st.text)
        types = [e["type"] for e in evs]
        assert types[-1] == "done" and "investigation_started" in types and "token" in types
        assert [e["seq"] for e in evs] == sorted(e["seq"] for e in evs)
        # yeniden bağlanma: Last-Event-ID'den sonrası
        again = sse((await c.get(run_["stream_url"], headers={**sre, "Last-Event-ID": str(evs[-3]["seq"])})).text)
        assert [e["seq"] for e in again] == [e["seq"] for e in evs[-2:]]
        cid = run_["conversation_id"]
        msgs = (await c.get(f"/api/conversations/{cid}/messages", headers=sre)).json()
        assert [m["role"] for m in msgs["items"]] == ["user", "assistant"]
        assert msgs["items"][1]["metadata"]["intent"] == "analyze"
        inv_id = msgs["items"][1]["investigation_id"]
        conv = (await c.get(f"/api/conversations/{cid}", headers=sre)).json()
        assert conv["resumable"] is False and conv["active_run_id"] is None
        # başka kullanıcı bu konuşmayı göremez
        other = await mkuser(c, h, "baska", "sre")
        assert (await c.get(f"/api/conversations/{cid}", headers=other)).status_code == 404
        assert (await c.get(run_["stream_url"], headers=other)).status_code == 404
        inv = (await c.get(f"/api/investigations/{inv_id}", headers=sre)).json()
        assert inv["status"] == "COMPLETED" and inv["primary_mechanism"] == "row_lock" and inv["report"]
        tools = (await c.get(f"/api/investigations/{inv_id}/tools", headers=sre)).json()
        assert len(tools) >= 17 and {"app_metrics", "db_metrics", "correlate", "rca"} <= {t["step"] for t in tools}
        ev = (await c.get(f"/api/investigations/{inv_id}/evidence", headers=sre)).json()
        assert any(e["evidence_type"] == "TRACE" for e in ev) and all("records" not in e["metadata"] or
                                                                      isinstance(e["metadata"]["records"], int)
                                                                      for e in ev)
        lst = (await c.get("/api/investigations?q=checkin&status=COMPLETED,PARTIAL", headers=sre)).json()
        assert lst["total"] == 1
        convs = (await c.get("/api/conversations?q=yavaşladı", headers=sre)).json()
        assert convs["total"] == 1
        return inv_id
    with_client(api_settings, tmp_path, fn)


def test_incident_rca_feedback_approval_flow(api_settings, tmp_path):
    async def fn(c, app):
        h = await login(c)
        sre = await mkuser(c, h, "sre2", "sre")
        r = await c.post("/api/agent/investigate", headers=sre, json={"time_range": TR, "title": "E2 elle"})
        assert r.status_code == 202, r.text
        evs = sse((await c.get(r.json()["stream_url"], headers=sre)).text)
        assert "investigation_completed" in [e["type"] for e in evs]
        inv_id = r.json()["investigation_id"]
        inc = (await c.post("/api/incidents", headers=sre, json={"title": "Checkin gecikmesi", "severity": "high",
                                                                 "application_id": "checkin-service",
                                                                 "investigation_id": inv_id})).json()
        assert inc["status"] == "OPEN" and inc["allowed_transitions"] == ["INVESTIGATING", "MITIGATED", "RESOLVED"]
        p = await c.patch(f"/api/incidents/{inc['id']}", headers=sre, json={"status": "INVESTIGATING", "note": "bakıyoruz",
                                                                             "root_cause": "hot row"})
        assert p.status_code == 200 and p.json()["status"] == "INVESTIGATING" and p.json()["root_cause"] == "hot row"
        bad = await c.patch(f"/api/incidents/{inc['id']}", headers=sre, json={"status": "OPEN"})
        assert bad.status_code == 409 and bad.json()["error"] == "invalid_transition"
        g = await c.post(f"/api/rca/{inv_id}/generate", headers=sre, json={"incident_id": inc["id"]})
        assert g.status_code == 201, g.text
        rc = g.json()
        assert rc["suspected_root_cause"].startswith("Row lock wait") and rc["confidence"] == "high"
        assert rc["timeline"] and rc["supporting_evidence"] and "## Güven / belirsizlik" in rc["markdown"]
        md = await c.get(f"/api/rca/{rc['id']}/markdown", headers=sre)
        assert md.status_code == 200 and md.text.startswith("# RCA:") and "attachment" in md.headers["content-disposition"]
        full = (await c.get(f"/api/incidents/{inc['id']}", headers=sre)).json()
        assert len(full["rca_reports"]) == 1 and full["investigations"][0]["relation"] == "trigger"
        assert [e["to_status"] for e in full["timeline"] if e["kind"] == "status"] == ["OPEN", "INVESTIGATING"]
        f1 = await c.post("/api/feedback", headers=sre, json={"investigation_id": inv_id, "verdict": "CORRECT",
                                                              "rating": 5, "rca_id": rc["id"]})
        f2 = await c.post("/api/feedback", headers=sre, json={"investigation_id": inv_id,
                                                              "verdict": "PARTIALLY_CORRECT", "rating": 3})
        assert f1.status_code == f2.status_code == 201 and f1.json()["id"] == f2.json()["id"]
        assert (await c.get("/api/feedback/summary", headers=sre)).json() == {"PARTIALLY_CORRECT": 1}
        ap = (await c.post("/api/approvals", headers=sre, json={"action_type": "RESTART", "target": "checkin-service",
                                                                "justification": "havuz tükendi, yeniden başlatma",
                                                                "incident_id": inc["id"]})).json()
        assert (await c.post(f"/api/approvals/{ap['id']}/decision", headers=sre,
                             json={"approve": True})).status_code == 403          # sre karar veremez
        d = await c.post(f"/api/approvals/{ap['id']}/decision", headers=h, json={"approve": True, "comment": "ok"})
        assert d.status_code == 200 and d.json()["status"] == "APPROVED"
        mine = await mkuser(c, h, "admin2", "admin")
        ap2 = (await c.post("/api/approvals", headers=mine, json={"action_type": "SQL_WRITE", "target": "db",
                                                                  "justification": "uzun transaction sonlandır"})).json()
        assert (await c.post(f"/api/approvals/{ap2['id']}/decision", headers=mine,
                             json={"approve": True})).status_code == 409           # dört göz
    with_client(api_settings, tmp_path, fn)


def test_dashboard_reports_missing_data_honestly(api_settings, tmp_path):
    async def fn(c, app):
        h = await login(c)
        d = (await c.get("/api/dashboard/summary?range=1h", headers=h)).json()
        assert d["available"] is True and d["monitored_applications"] == 4
        assert d["status_counts"] == {"unknown": 4}
        assert d["overall"]["throughput_rpm"] is None and d["overall"]["p95_ms_worst"] is None
        a = (await c.get("/api/applications/checkin-service/metrics?range=1h", headers=h)).json()
        assert a["status"] == "unknown" and a["metrics"]["p95_ms"] is None and a["series"] == []
        assert (await c.get("/api/dashboard/summary?range=7d", headers=h)).status_code == 422
        one = (await c.get("/api/dashboard/summary?application_id=checkin-service", headers=h)).json()
        assert set(one["overall"]) == set(d["overall"]) and one["overall"]["p95_ms_worst"] is None  # aynı sözleşme
        assert one["monitored_applications"] == 1
    def empty(on_event=None):
        from synth import FakeGateway
        return FakeGateway({})
    with_client(api_settings, tmp_path, fn, gw=empty)

    def down(on_event=None):
        from synth import FakeGateway
        return FakeGateway(down=True)

    async def fn2(c, app):
        h = await login(c)
        d = (await c.get("/api/dashboard/summary", headers=h)).json()
        assert d["available"] is False and "outage" in d["error"] and d["overall"] == {}
    with_client(api_settings, tmp_path, fn2, gw=down)


# ---------------------------------------------------------------- telemetri hesapları (DB gerektirmez)
def _ts_rec(start, count, iv_min, **fields):
    return {"timeframe": {"start": start.isoformat().replace("+00:00", "Z"), "end": ""},
            "interval": str(iv_min * 60 * 10**9), **fields}


def test_telemetry_compute_math_and_status():
    from sre_platform.services.telemetry import compute, make_span
    c = cfg()
    sp = make_span("15m", 120, now=datetime(2026, 11, 21, 12, 0, tzinfo=timezone.utc))
    s0 = sp.start
    QR = lambda k, recs: QueryResult(k, "", "OK" if recs else "EMPTY", recs)  # noqa: E731
    res = {
        "http": QR("http", [
            _ts_rec(s0, 15, 1, service="checkin-service", outcome="SUCCESS", n=[60.0] * 15, t=[600.0] * 15),
            _ts_rec(s0, 15, 1, service="checkin-service", outcome="SERVER_ERROR", n=[3.0] * 15, t=[30.0] * 15),
            _ts_rec(s0, 15, 1, service="reservation-service", outcome="SUCCESS", n=[30.0] * 15, t=[300.0] * 15)]),
        "spans_total": QR("spans_total", [{"svc": "checkin-service", "n": "900", "p50": "8", "p95": "90", "p99": "150"},
                                          {"svc": "reservation-service", "n": "400", "p50": "5", "p95": "20",
                                           "p99": "30"}]),
        "spans_prev": QR("spans_prev", [{"svc": "checkin-service", "n": "900", "p95": "30"}]),
        "spans_series": QR("spans_series", []),
        "hikari": QR("hikari", [_ts_rec(s0, 15, 1, service="reservation-service", a=[8.0] * 15, mx=[8.0] * 15,
                                        pe=[2.0] * 15)]),
        "db_conn": QR("db_conn", [_ts_rec(s0, 15, 1, r=[0.2] * 14 + [0.4], used=[20.0] * 15, slots=[100.0] * 15)]),
        "containers": QR("containers", [{"name": f"airlab/{x}:1.0.0", "u": [1.0, 1.0]}
                                        for x in ("checkin", "reservation", "flightops")]),
    }
    out = compute(res, sp, c)
    ck, rs, fo, bg = (out["apps"][a] for a in ("checkin-service", "reservation-service", "flightops-service",
                                               "baggage-service"))
    assert ck["throughput_rpm"] == 63.0 and ck["error_rate"] == round(3 / 63, 4) and ck["mean_ms"] == 10.0
    assert ck["status"] == "degraded" and any("error rate" in x for x in ck["reasons"]) \
        and any("p95 90 ms" in x for x in ck["reasons"])
    assert rs["status"] == "degraded" and rs["pool"]["last_ratio"] == 1.0 and rs["pool"]["pending_max"] == 2.0
    assert fo["status"] == "unknown" and fo["throughput_rpm"] is None      # container var, istek yok → veri yok
    assert bg["status"] == "down" and bg["reasons"] == ["container metric'i gelmiyor"]
    assert out["overall"]["p95_ms_worst"] == 90 and out["overall"]["p99_ms_worst"] == 150
    assert out["db"]["last_ratio"] == 0.4 and out["db"]["max_ratio"] == 0.4 and len(ck["series"]) == 15
    assert out["status_counts"] == {"degraded": 2, "unknown": 1, "down": 1}
    _ = timedelta


class _HalfStream(FakePort):
    """LLM yanıtı akarken kesilir (ilk parçalar istemciye gitmiş olur)."""

    async def astream(self, system, human, *, rca=False, task="answer"):
        from airlab_agent.llm import LlmUnavailable
        yield "YARIM "
        yield "METIN "
        raise LlmUnavailable("simulated mid-stream")


def _gw_with_failed(failed: dict):
    from scenarios import SCENARIOS
    from synth import build
    from test_agent_memory import EmittingGateway
    base, win, extra = SCENARIOS["row_lock"]()
    results = build(base, win, WS, **extra)
    for k, status in failed.items():
        results[k] = QueryResult(k, results[k].dql, status, [], None, "simulated failure")

    def make(on_event=None):
        return EmittingGateway(results, on_event=on_event)
    return make


def test_failed_query_and_llm_outage_are_reported_as_such(api_settings, tmp_path):
    """Yanıt vermeyen query "0" ya da "başarılı" görünmez; yarıda kesilen LLM yanıtı ekranda ve kayıtta kalmaz."""
    async def fn(c, app):
        h = await login(c)
        sre = await mkuser(c, h, "nobetci2", "sre")
        r = await c.post("/api/agent/chat", headers=sre, json={"message": "Checkin neden yavaşladı?", "time_range": TR})
        assert r.status_code == 202, r.text
        run_ = r.json()
        evs = sse((await c.get(run_["stream_url"], headers=sre)).text)
        types = [e["type"] for e in evs]
        assert types[-1] == "done"
        # yarım metin gönderildikten sonra istemciye "yerine geç" olayı gider
        i_half = next(i for i, e in enumerate(evs) if e["type"] == "token" and "YARIM" in str(e["data"].get("text")))
        i_rep = next(i for i, e in enumerate(evs) if e["type"] == "answer_replaced")
        assert i_rep > i_half and evs[i_rep]["data"]["reason"] == "llm_unavailable"
        msgs = (await c.get(f"/api/conversations/{run_['conversation_id']}/messages", headers=sre)).json()
        m = msgs["items"][1]
        assert "YARIM" not in m["content"]
        assert m["metadata"]["answer_source"] == "deterministik (Claude erişilemedi)"
        assert "Okunamayan signal (1): jvm_gc" in m["content"] and "0 sayılmaz" in m["content"]
        inv = (await c.get(f"/api/investigations/{m['investigation_id']}", headers=sre)).json()
        assert inv["status"] == "PARTIAL" and inv["primary_mechanism"] == "row_lock"
        tools = (await c.get(f"/api/investigations/{m['investigation_id']}/tools", headers=sre)).json()
        t = next(t for t in tools if t["signal"] == "jvm_gc")
        assert t["execution_status"] == "FAILED" and t["result_status"] == "TIMEOUT"
        ok = next(t for t in tools if t["signal"] == "http")
        assert ok["execution_status"] == "SUCCEEDED"
    with_client(api_settings, tmp_path, fn, gw=_gw_with_failed({"jvm_gc": "TIMEOUT"}), llm=_HalfStream())
