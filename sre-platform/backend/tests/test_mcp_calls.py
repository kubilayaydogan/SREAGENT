"""MCP Çağrıları sayfası (backend). Çağrılar GERÇEK McpGateway üzerinden kaydedilir; yalnız MCP oturumu sahtedir
(tools/call yanıtı / timeout / HTTP hatası), PostgreSQL gerçektir.

Doğrulananlar: başarı, boş sonuç, retry (aynı mantıksal çağrıda iki deneme), timeout, HTTP 200 içinde MCP tool hatası
(başarısız), HTTP 401 yetki hatası (tekrar denenmez), gönderilmeyen çağrının listede olmaması, filtreler ve sunucu
tarafı sayfalama, ham DQL / yanıt / token'ın kayda ve API'ye girmemesi, kayıt yazılamadığında analiz sonucunun
değişmemesi ve yetkisiz erişim (kimliksiz 401, viewer 403, scope'suz API anahtarı 403).
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import sys
from pathlib import Path
from types import SimpleNamespace as NS

ROOT = Path(__file__).resolve().parents[3]
sys.path[:0] = [str(ROOT / "sre-agent/tests"), str(ROOT / "sre-agent/eval")]

from sqlalchemy import select, text  # noqa: E402
from synth import build, windows  # noqa: E402
from test_api import api_settings, lg_ready, login, mkuser, with_client  # noqa: E402,F401
from test_scenarios import SCENARIOS  # noqa: E402

from airlab_agent.graph import new_run_id  # noqa: E402
from airlab_agent.mcp_gateway import McpGateway  # noqa: E402
from sre_platform.agent.investigation import InvestigationRunner  # noqa: E402
from sre_platform.db.models import ToolExecution, Trigger  # noqa: E402
from sre_platform.db.session import transaction  # noqa: E402
from sre_platform.repositories import applications, investigations  # noqa: E402

WS = windows()
PLAIN = "PLAIN-SECRET-VALUE-42"           # hiçbir redaksiyon kalıbına uymayan gizli değer
TOKEN = "dt0s16.SAMPLE-PLATFORM-TOKEN-XYZ"


class HttpError(Exception):
    """httpx.HTTPStatusError benzeri: yanıt nesnesinde status_code."""

    def __init__(self, code):
        super().__init__(f"Client error '{code}' for url 'http://mcp.test/x?token={PLAIN}'")
        self.response = NS(status_code=code)


class Session:
    """tools/call davranışı anahtar ve deneme sırasına göre: ok | empty | timeout | toolerr | perm200 | http401."""

    def __init__(self, results, plan):
        self.results, self.plan, self.dql_key, self.n = results, plan, {}, {}
        self.calls: list[dict] = []

    async def call_tool(self, name, args):
        self.calls.append({"name": name, **args})
        key = self.dql_key.get(args["dqlQueryString"], "?")
        i = self.n[key] = self.n.get(key, -1) + 1
        steps = self.plan.get(key, ["ok"])
        beh = steps[min(i, len(steps) - 1)]
        await asyncio.sleep(0.005)
        if beh == "timeout":
            await asyncio.sleep(5)
        if beh == "http401":
            raise HttpError(401)
        if beh == "toolerr":                    # HTTP 200, MCP isError=True
            return NS(isError=True, content=[NS(text=f'DQL syntax error near "{PLAIN}"\nfetch logs ...')],
                      structuredContent=None)
        if beh == "perm200":
            return NS(isError=True, content=[NS(text="missing required scope storage:logs:read")],
                      structuredContent=None)
        r = self.results.get(key)
        recs = [] if beh == "empty" or r is None else [{**x, "content": f"log line {PLAIN}"} for x in r.records]
        return NS(isError=False, content=[], structuredContent={"records": recs,
                                                                "metadata": {"grail": {"queryId": f"q-{key}"}}})


def gateway(plan):
    base, win, extra = SCENARIOS["row_lock"]()
    results = build(base, win, WS, **extra)
    holder: dict = {}

    class Gw(McpGateway):
        def run_many(self, items):
            holder["s"].dql_key.update({d: k for k, d in items})
            return super().run_many(items)

    def make(on_event=None):
        sess = holder.setdefault("s", Session(results, plan))

        @contextlib.asynccontextmanager
        async def cm(_server):
            yield sess
        g = Gw("http://mcp.test", TOKEN, timeout_s=0.5, retries=1, concurrency=4, on_event=on_event)
        g.client = NS(session=cm)
        return g
    return make, holder


async def _noop(*a, **k):
    return None


async def _run(app, mode="manual"):
    rt = app.state.rt
    async with transaction(rt.sm) as s:
        await applications.sync_from_config(s, rt.agent.cfg)
        inv = await investigations.create(s, run_id=new_run_id(mode[:3]), trigger=Trigger.MANUAL, title=mode,
                                          window_start=WS[1].start, window_end=WS[1].end)
        inv_id, run_id = inv.id, inv.run_id
    await InvestigationRunner(rt).run(inv_id, emit=_noop, mode=mode)
    async with transaction(rt.sm) as s:
        inv = await investigations.get(s, inv_id)
        return inv_id, run_id, inv.status, inv.risk_score


PLAN = {"http": ["ok"], "hikari": ["empty"], "db_conn": ["timeout", "ok"], "pg_lock_logs": ["toolerr"],
        "statements": ["perm200"]}


def test_call_classes_retry_detail_filters_and_no_raw_data(api_settings, tmp_path):
    gw, holder = gateway(PLAN)

    async def fn(c, app):
        h = await login(c)
        inv_id, run_id, *_ = await _run(app)
        inv2, run2, *_ = await _run(app, "scheduled")
        lst = (await c.get("/api/mcp-calls", headers=h, params={"run_id": run_id, "size": 200})).json()
        rows = {r["query_key"]: r for r in lst["items"]}
        details = {k: (await c.get(f"/api/mcp-calls/{r['id']}", headers=h)).json() for k, r in rows.items()}
        page1 = (await c.get("/api/mcp-calls", headers=h, params={"size": 5})).json()
        page2 = (await c.get("/api/mcp-calls", headers=h, params={"size": 5, "page": 2})).json()
        failed = (await c.get("/api/mcp-calls", headers=h,
                              params={"status": "timeout,tool_error,permission_denied", "run_id": run_id})).json()
        by_tool = (await c.get("/api/mcp-calls", headers=h, params={"tool": "nope"})).json()
        by_conn = (await c.get("/api/mcp-calls", headers=h, params={"connector": "dynatrace", "size": 1})).json()
        future = (await c.get("/api/mcp-calls", headers=h, params={"since": "2099-01-01T00:00:00Z"})).json()
        bad = await c.get("/api/mcp-calls", headers=h, params={"status": "weird"})
        facets = (await c.get("/api/mcp-calls/facets", headers=h)).json()
        inv_tools = (await c.get(f"/api/investigations/{inv_id}/tools", headers=h)).text
        async with transaction(app.state.rt.sm) as s:
            raw = json.dumps([[str(v) for v in r] for r in (await s.execute(text(
                "SELECT tool_arguments, request_preview, attempts, error_message FROM sre.agent_tool_executions"
            ))).all()])
            n_db = (await s.execute(select(ToolExecution).where(ToolExecution.investigation_id == inv_id,
                                                                ToolExecution.mcp_method.is_not(None)))).all()
        api_blob = json.dumps([lst, details, page1, failed]) + inv_tools
        return locals()
    r = with_client(api_settings, tmp_path, fn, gw=gw)
    rows, d = r["rows"], r["details"]

    # her mantıksal çağrı bir satır; satır sayısı = gerçekten gönderilen tools/call'ların mantıksal sayısı
    assert r["lst"]["total"] == len(r["n_db"]) == len(rows)
    assert rows["http"]["outcome"] == "success" and rows["http"]["record_count"] > 0 and not rows["http"]["failed"]
    assert rows["hikari"]["outcome"] == "empty" and rows["hikari"]["record_count"] == 0 and not rows["hikari"]["failed"]
    # retry: tek satır, detayda iki deneme (timeout → success)
    assert rows["db_conn"]["outcome"] == "success" and rows["db_conn"]["attempts"] == 2
    assert [(a["attempt"], a["outcome"]) for a in d["db_conn"]["attempts_log"]] == [(1, "timeout"), (2, "success")]
    assert all(a["elapsed_ms"] is not None for a in d["db_conn"]["attempts_log"])
    # HTTP 200 içinde MCP tool hatası → başarısız; kayıt sayısı uygulanamaz → boş
    for k, oc in (("pg_lock_logs", "tool_error"), ("statements", "permission_denied")):
        assert rows[k]["outcome"] == oc and rows[k]["failed"] and rows[k]["record_count"] is None
        assert d[k]["response"]["http_status"] == 200 and d[k]["response"]["failed"]
        assert d[k]["response"]["error_summary"]
    # detay: istek önizlemesi, DQL şablonu ve pencere, ilişkili çalışma
    req, q, rel = d["http"]["request"], d["http"]["query"], d["http"]["related"]
    assert req["method"] == "tools/call" and req["tool"] == "execute-dql" and req["connector"] == "dynatrace"
    assert req["query_key"] == "http" and req["query_start"] and req["query_end"] and req["params"] == {
        "includeTypes": False}
    assert q["window"] in ("incident", "baseline") and '"…"' in q["dql_template"] and q["dql_sha256"]
    assert rel == {**rel, "mcp_call_id": d["http"]["id"], "run_id": r["run_id"], "investigation_id": str(r["inv_id"]),
                   "step": rows["http"]["step"]}
    assert d["http"]["response"]["evidence_key"] and d["http"]["response"]["http_status"] == 200
    windows_seen = {x["query"]["window"] for x in d.values() if x["query"]}
    assert "incident" in windows_seen
    # sayfalama, sıralama ve filtreler sunucu tarafında
    p1, p2 = r["page1"], r["page2"]
    assert len(p1["items"]) == 5 and p1["total"] == r["lst"]["total"] * 2 and p1["pages"] >= 2
    assert not {x["id"] for x in p1["items"]} & {x["id"] for x in p2["items"]}
    times = [x["started_at"] for x in p1["items"] + p2["items"]]
    assert times == sorted(times, reverse=True)                                   # en yeni üstte
    assert p1["items"][0]["run_id"] == r["run2"]
    assert {x["outcome"] for x in r["failed"]["items"]} == {"tool_error", "permission_denied"}
    assert r["by_tool"]["total"] == 0 and r["future"]["total"] == 0 and r["bad"].status_code == 422
    assert r["by_conn"]["total"] == p1["total"] and len(r["by_conn"]["items"]) == 1
    assert r["facets"]["connectors"] == ["dynatrace"] and r["facets"]["tools"] == ["execute-dql"]
    # ham DQL, ham yanıt (log içeriği), token kayda ve API'ye girmez
    sent_dql = [x["dqlQueryString"] for x in holder["s"].calls]
    for blob in (r["raw"], r["api_blob"]):
        assert PLAIN not in blob and TOKEN not in blob and "log line" not in blob
        assert not any(dq in blob for dq in sent_dql)
        assert "dqlQueryString" not in blob


def test_timeout_and_http_401(api_settings, tmp_path):
    """Tüm denemeleri zaman aşımına uğrayan çağrı timeout; HTTP 401 yetki hatası tekrar denenmez. Bu çağrılar MCP'yi
    erişilemez kılar (analiz kısmi raporla biter); her çağrının kaydı yine kapanır (STARTED'da kalmaz)."""
    def run_with(plan):
        async def fn(c, app):
            h = await login(c)
            _, run_id, status, _ = await _run(app)
            lst = (await c.get("/api/mcp-calls", headers=h, params={"run_id": run_id, "size": 200})).json()
            rows = {r["query_key"]: r for r in lst["items"]}
            det = {k: (await c.get(f"/api/mcp-calls/{r['id']}", headers=h)).json() for k, r in rows.items()}
            async with transaction(app.state.rt.sm) as s:
                open_ = (await s.execute(text("SELECT count(*) FROM sre.agent_tool_executions "
                                              "WHERE execution_status = 'STARTED'"))).scalar_one()
            return status, rows, det, open_
        return fn

    status, rows, det, open_ = with_client(api_settings, tmp_path, run_with({}), gw=gateway({"http": ["timeout"]})[0])
    assert rows["http"]["outcome"] == "timeout" and rows["http"]["failed"] and rows["http"]["record_count"] is None
    assert [a["outcome"] for a in det["http"]["attempts_log"]] == ["timeout", "timeout"]
    assert det["http"]["response"]["http_status"] is None and status.value in ("PARTIAL", "FAILED")
    assert open_ == 0 and all(r["outcome"] for r in rows.values())

    status, rows, det, open_ = with_client(api_settings, tmp_path, run_with({}), gw=gateway({"http": ["http401"]})[0])
    assert rows["http"]["outcome"] == "permission_denied" and rows["http"]["attempts"] == 1
    assert det["http"]["response"]["http_status"] == 401 and PLAIN not in json.dumps(det)
    assert open_ == 0


def test_record_write_failure_does_not_change_result(api_settings, tmp_path, monkeypatch):
    from sre_platform.repositories import investigations as repo

    async def fn(c, app):
        ok = await _run(app)
        orig_s, orig_f = repo.tool_started, repo.tool_finished

        async def boom(*a, **k):
            raise RuntimeError("db down")
        monkeypatch.setattr(repo, "tool_started", boom)
        monkeypatch.setattr(repo, "tool_finished", boom)
        broken = await _run(app)
        monkeypatch.setattr(repo, "tool_started", orig_s)
        monkeypatch.setattr(repo, "tool_finished", orig_f)
        return ok, broken
    ok, broken = with_client(api_settings, tmp_path, fn, gw=gateway({})[0])
    assert broken[2] == ok[2] and broken[3] == ok[3]                    # durum ve risk aynı


def test_unauthorized_access(api_settings, tmp_path):
    async def fn(c, app):
        h = await login(c)
        inv_id, run_id, *_ = await _run(app)
        cid = (await c.get("/api/mcp-calls", headers=h)).json()["items"][0]["id"]
        viewer = await mkuser(c, h, "izleyici", "viewer")
        sre = await mkuser(c, h, "nobetci", "sre")
        k_no = (await c.post("/api/api-keys", headers=h, json={"name": "bot1", "scopes": ["record:read"],
                                                               "days": 1})).json()["token"]
        k_ok = (await c.post("/api/api-keys", headers=h, json={"name": "bot2", "scopes": ["observability:read"],
                                                               "days": 1})).json()["token"]
        out = {}
        for who, hh in (("anon", {}), ("viewer", viewer), ("sre", sre), ("key_no", {"Authorization": f"Bearer {k_no}"}),
                        ("key_ok", {"Authorization": f"Bearer {k_ok}"})):
            out[who] = [(await c.get(u, headers=hh)).status_code
                        for u in ("/api/mcp-calls", f"/api/mcp-calls/{cid}", "/api/mcp-calls/facets")]
        out["missing"] = (await c.get("/api/mcp-calls/00000000-0000-0000-0000-000000000000", headers=h)).status_code
        return out
    out = with_client(api_settings, tmp_path, fn, gw=gateway({})[0])
    assert out["anon"] == [401, 401, 401]
    assert out["viewer"] == [403, 403, 403] and out["key_no"] == [403, 403, 403]
    assert out["sre"] == [200, 200, 200] and out["key_ok"] == [200, 200, 200]
    assert out["missing"] == 404


def test_migration_0013_scrubs_legacy_raw_dql(db_url):
    """0012 öncesi biçimdeki kayıt: ham DQL tool_arguments'tan silinir, güvenli önizleme üretilir; zaten önizlemesi
    olan kayıtta önizleme korunur."""
    import sqlalchemy as sa
    from sre_platform.db import migrate
    migrate.downgrade(db_url, "0012")
    dql = (f'fetch logs, from:"2026-11-21T10:00:00Z", to:"2026-11-21T10:15:00Z" | filter content == "{PLAIN}" '
           '| limit 50')
    eng = sa.create_engine(db_url, poolclass=sa.pool.NullPool)
    with eng.begin() as c:
        inv = c.execute(sa.text(
            "INSERT INTO sre.investigations (run_id, trigger, title, status, severity, window_start, window_end) "
            "VALUES ('legacy-1', 'manual', 'eski', 'COMPLETED', 'unknown', now() - interval '1 hour', now()) "
            "RETURNING id")).scalar_one()
        for sig, prev in (("pg_lock_logs", "{}"), ("http", '{"dql_template": "korunur"}')):
            c.execute(sa.text(
                "INSERT INTO sre.agent_tool_executions (investigation_id, tool_name, signal, step, tool_arguments, "
                "execution_status, result_status, mcp_method, connector, outcome, request_preview) VALUES "
                "(:i, 'execute-dql', :s, 'logs', CAST(:a AS jsonb), 'SUCCEEDED', 'OK', 'tools/call', 'dynatrace', "
                "'success', CAST(:p AS jsonb))"),
                {"i": inv, "s": sig, "a": json.dumps({"dqlQueryString": dql, "mcp_call_id": "x"}), "p": prev})
    migrate.upgrade(db_url)
    with eng.connect() as c:
        rows = {r.signal: r for r in c.execute(sa.text(
            "SELECT signal, tool_arguments, request_preview FROM sre.agent_tool_executions"))}
    eng.dispose()
    assert migrate.current_revision(db_url) == "0013"
    blob = json.dumps({k: [r.tool_arguments, r.request_preview] for k, r in rows.items()}, ensure_ascii=False)
    assert PLAIN not in blob and dql not in blob and "dqlQueryString" not in blob
    a, p = rows["pg_lock_logs"].tool_arguments, rows["pg_lock_logs"].request_preview
    assert a == {"mcp_call_id": "x", "query_key": "pg_lock_logs"}
    assert p["dql_template"].startswith('fetch logs, from:"…", to:"…" | filter content == "…"') and p["backfilled"]
    assert (p["query_start"], p["query_end"], p["limit"]) == ("2026-11-21T10:00:00Z", "2026-11-21T10:15:00Z", 50)
    assert rows["http"].request_preview == {"dql_template": "korunur"}
