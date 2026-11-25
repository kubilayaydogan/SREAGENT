"""Settings modülü: öncelik/kaynak, kilitler, revizyon çakışması, doğrulama, RBAC, audit, rollback, secret maskeleme,
SSRF koruması, zamanlayıcı senkronu, run-now, risk önizlemesi, koşu snapshot'ı ve tetikleyici korumaları.
Gerçek PostgreSQL; sahte MCP/Claude (test_api yardımcıları)."""
from __future__ import annotations

import pytest

from test_api import TR, api_settings, lg_ready, login, mkuser, sse, with_client  # noqa: F401

KEY = "k" * 40


FAKE_DNS = {"mcp.example.com": ["93.184.215.14"], "example.com": ["93.184.215.14"], "localhost": ["127.0.0.1"],
            "abc.apps.dynatrace.com": ["3.120.0.10"]}


@pytest.fixture
def settings_env(monkeypatch):
    monkeypatch.setenv("SRE_SETTINGS_KEY", KEY)
    monkeypatch.setenv("SRE_SETTINGS_LOCKS", "agent.max_concurrent_runs=2")
    from sre_platform.settings import netguard

    def resolve(host, port):                     # test ortamında DNS'e bağımlı olmamak için sabit çözümleme
        if host in FAKE_DNS:
            return FAKE_DNS[host]
        try:
            import ipaddress
            return [str(ipaddress.ip_address(host))]
        except ValueError:
            raise netguard.UnsafeUrl(f"DNS çözümlenemedi: {host}") from None
    monkeypatch.setattr(netguard, "_resolve", resolve)


def test_netguard_rules(settings_env, monkeypatch):
    from sre_platform.settings.netguard import UnsafeUrl, check_url
    assert check_url("https://mcp.example.com/mcp")
    for bad in ("https://127.0.0.1/", "https://[::1]/", "https://169.254.169.254/", "https://192.168.1.10/",
                "http://mcp.example.com/", "ftp://mcp.example.com/", "https://u:p@mcp.example.com/",
                "https://nope.invalid/"):
        with pytest.raises(UnsafeUrl):
            check_url(bad)
    monkeypatch.setenv("SRE_OUTBOUND_ALLOWLIST", "10.0.0.0/8,*.corp.local")
    assert check_url("https://10.1.2.3/mcp")
    with pytest.raises(UnsafeUrl):
        check_url("https://10.1.2.3:443@evil/")


def test_sections_sources_locks_rbac(api_settings, tmp_path, settings_env):
    async def fn(c, app):
        h = await login(c)
        sre = await mkuser(c, h, "sre1", "sre")
        viewer = await mkuser(c, h, "view1", "viewer")
        assert (await c.get("/api/settings", headers=viewer)).status_code == 403
        assert (await c.get("/api/settings/public", headers=viewer)).status_code == 200
        ov = (await c.get("/api/settings", headers=sre)).json()
        assert {s["section"] for s in ov["sections"]} >= {"general", "agent", "scheduling", "risk", "llm",
                                                           "observability", "notifications", "retention"}
        ag = (await c.get("/api/settings/agent", headers=sre)).json()
        f = ag["fields"]
        assert f["max_concurrent_runs"]["source"] == "locked" and f["max_concurrent_runs"]["locked"]
        assert f["recursion_limit"]["source"] == "default" and f["recursion_limit"]["value"] == 25
        assert f["max_concurrent_runs"]["apply"] == "restart"
        # sre okuyabilir, değiştiremez
        r = await c.patch("/api/settings/agent", headers=sre, json={"revision": 0, "values": {"recursion_limit": 30}})
        assert r.status_code == 403
        # kilitli alan reddedilir ve audit'e "rejected" yazılır
        r = await c.patch("/api/settings/agent", headers=h, json={"revision": 0, "values": {"max_concurrent_runs": 4}})
        assert r.status_code == 422 and r.json()["error"] == "invalid_settings"
        aud = (await c.get("/api/settings/audit?section=agent", headers=sre)).json()
        assert aud["items"][0]["result"] == "rejected"
        # admin kaydı → kaynak "admin", revizyon 1
        r = await c.patch("/api/settings/agent", headers=h, json={"revision": 0, "values": {"recursion_limit": 30},
                                                                   "note": "deneme"})
        assert r.status_code == 200, r.text
        assert r.json()["revision"] == 1 and r.json()["fields"]["recursion_limit"]["source"] == "admin"
        # bilinmeyen alan / bölüm
        bad = await c.patch("/api/settings/agent", headers=h, json={"revision": 1, "values": {"nope": 1}})
        assert bad.status_code == 422
        assert (await c.get("/api/settings/unknown", headers=sre)).status_code == 404
    with_client(api_settings, tmp_path, fn)


def test_conflict_validation_rollback_audit(api_settings, tmp_path, settings_env):
    async def fn(c, app):
        h = await login(c)
        r1 = await c.patch("/api/settings/risk", headers=h, json={"revision": 0, "values": {"min_score_coverage": 0.5}})
        assert r1.status_code == 200 and r1.json()["revision"] == 1
        stale = await c.patch("/api/settings/risk", headers=h, json={"revision": 0, "values": {"min_score_coverage": 0.6}})
        assert stale.status_code == 409 and stale.json()["error"] == "revision_conflict"
        assert stale.json()["details"]["current_revision"] == 1
        w = dict(r1.json()["values"]["weights"])
        w["latency"] += 0.2                                            # toplam ≠ 1
        inv = await c.patch("/api/settings/risk", headers=h, json={"revision": 1, "values": {"weights": w}})
        assert inv.status_code == 422
        bands = await c.patch("/api/settings/risk", headers=h,
                              json={"revision": 1, "values": {"bands": {"low": 60, "medium": 50, "high": 80}}})
        assert bands.status_code == 422
        r2 = await c.patch("/api/settings/risk", headers=h, json={"revision": 1, "values": {"min_score_coverage": 0.7}})
        assert r2.json()["revision"] == 2
        revs = (await c.get("/api/settings/risk/revisions", headers=h)).json()["items"]
        assert [x["revision"] for x in revs][:2] == [2, 1]
        rb = await c.post("/api/settings/risk/rollback", headers=h, json={"revision": 2, "to_revision": 1})
        assert rb.status_code == 200, rb.text
        assert rb.json()["revision"] == 3 and rb.json()["values"]["min_score_coverage"] == 0.5   # yeni revizyon
        aud = (await c.get("/api/settings/audit?section=risk", headers=h)).json()["items"]
        assert aud[0]["action"] == "rollback" and aud[0]["actor"] == "admin"
        assert aud[0]["before"]["min_score_coverage"] == 0.7 and aud[0]["after"]["min_score_coverage"] == 0.5
        # reset: alan varsayılana döner
        rs = await c.patch("/api/settings/risk", headers=h, json={"revision": 3, "reset": ["min_score_coverage"]})
        assert rs.json()["fields"]["min_score_coverage"]["source"] in ("default", "env")
    with_client(api_settings, tmp_path, fn)


def test_secrets_are_never_returned_and_ssrf_blocked(api_settings, tmp_path, settings_env):
    async def fn(c, app):
        h = await login(c)
        sre = await mkuser(c, h, "sre3", "sre")
        val = "dt0s16.SUPERSECRETVALUE1234567890"
        r = await c.put("/api/settings/secrets/dt-token", headers=h, json={"value": val})
        assert r.status_code == 200 and val not in r.text and r.json()["ref"] == "secret:dt-token"
        assert (await c.put("/api/settings/secrets/x", headers=sre, json={"value": "v"})).status_code == 403
        lst = await c.get("/api/settings/secrets", headers=sre)
        assert lst.status_code == 200 and val not in lst.text
        assert any(x["name"] == "dt-token" for x in lst.json()["stored"])
        # SSRF: loopback / link-local / metadata / http / userinfo reddedilir
        for url in ("https://127.0.0.1/mcp", "https://169.254.169.254/latest", "http://example.com/mcp",
                    "https://user:pw@example.com/mcp", "https://localhost/mcp", "https://10.0.0.5/mcp"):
            r = await c.post("/api/settings/connectors", headers=h, json={
                "name": "evil", "provider": "custom", "endpoint": url, "auth_type": "bearer",
                "secret_ref": "secret:dt-token"})
            assert r.status_code == 422, (url, r.text)
        # env referansı yalnız izinli değişkenler
        r = await c.post("/api/settings/connectors", headers=h, json={
            "name": "envx", "provider": "custom", "endpoint": "https://mcp.example.com/mcp", "auth_type": "bearer",
            "secret_ref": "env:PATH"})
        assert r.status_code == 422
        # kullanımdaki secret silinemez
        ok = await c.post("/api/settings/connectors", headers=h, json={
            "name": "dt2", "provider": "custom", "endpoint": "https://mcp.example.com/mcp", "auth_type": "bearer",
            "secret_ref": "secret:dt-token"})
        assert ok.status_code == 201, ok.text
        assert ok.json()["read_only"] is True and val not in ok.text
        assert (await c.delete("/api/settings/secrets/dt-token", headers=h)).status_code == 409
        aud = await c.get("/api/settings/audit", headers=h)
        assert val not in aud.text
        # keşif yapılmadan tool onayı yok
        r = await c.patch(f"/api/settings/connectors/{ok.json()['id']}", headers=h,
                          json={"revision": 1, "allowed_tools": ["execute-dql"]})
        assert r.status_code == 422
    with_client(api_settings, tmp_path, fn)


def test_scheduler_sync_and_run_now(api_settings, tmp_path, settings_env):
    async def fn(c, app):
        from sre_platform.worker import ensure_jobs
        h = await login(c)
        rt = app.state.rt
        await ensure_jobs(rt)
        jobs = {j["name"]: j for j in (await c.get("/api/settings/scheduling/jobs", headers=h)).json()["items"]}
        assert {"health-check", "watch", "retention"} <= set(jobs)
        sch = (await c.get("/api/settings/scheduling", headers=h)).json()
        assert sch["status"]["cron"]["supported"] is False
        r = await c.patch("/api/settings/scheduling", headers=h, json={
            "revision": 0, "values": {"health_check_enabled": True, "health_check_interval_minutes": 30}})
        assert r.status_code == 200, r.text
        jobs = {j["name"]: j for j in (await c.get("/api/settings/scheduling/jobs", headers=h)).json()["items"]}
        assert jobs["health-check"]["interval_seconds"] == 1800 and jobs["health-check"]["enabled"]
        ok = await c.post("/api/settings/scheduling/jobs/health-check/run", headers=h)
        assert ok.status_code == 200 and ok.json()["queued"]
        from sqlalchemy import text
        from sre_platform.db.session import transaction
        async with transaction(rt.sm) as s:                           # iş şu an çalışıyor (kira) → çift koşu yok
            await s.execute(text("UPDATE sre.scheduled_jobs SET locked_until = now() + interval '5 min' "
                                 "WHERE name = 'health-check'"))
        busy = await c.post("/api/settings/scheduling/jobs/health-check/run", headers=h)
        assert busy.status_code == 409
        assert (await c.post("/api/settings/scheduling/jobs/nope/run", headers=h)).status_code == 404
        # manual mod: zamanlanmış health check çalıştırılamaz
        a = (await c.get("/api/settings/agent", headers=h)).json()
        await c.patch("/api/settings/agent", headers=h, json={"revision": a["revision"],
                                                               "values": {"execution_mode": "manual"}})
        assert (await c.post("/api/settings/scheduling/jobs/health-check/run", headers=h)).status_code == 409
    with_client(api_settings, tmp_path, fn)


def test_risk_preview_is_deterministic(api_settings, tmp_path, settings_env):
    async def fn(c, app):
        h = await login(c)
        p1 = (await c.post("/api/settings/risk/preview", headers=h, json={})).json()
        p2 = (await c.post("/api/settings/risk/preview", headers=h, json={})).json()
        assert p1 == p2
        rows = {r["id"]: r for r in p1["fixtures"]}
        assert rows["missing_data"]["current"]["score"] is None            # eksik veri 0 değil
        assert rows["healthy"]["current"]["score"] < rows["lock_contention"]["current"]["score"]
        assert all(r["delta"] in (0, 0.0, None) for r in p1["fixtures"])
        cur = (await c.get("/api/settings/risk", headers=h)).json()["values"]
        w = {"latency": 0.1, "errors": 0.1, "saturation": 0.1, "lock": 0.6, "resource": 0.1}
        p3 = (await c.post("/api/settings/risk/preview", headers=h, json={"weights": w})).json()
        lock = {r["id"]: r for r in p3["fixtures"]}["lock_contention"]
        assert lock["draft"]["score"] != lock["current"]["score"]
        assert (await c.get("/api/settings/risk", headers=h)).json()["values"] == cur     # önizleme kaydetmez
        bad = await c.post("/api/settings/risk/preview", headers=h, json={"weights": {**w, "lock": 0.9}})
        assert bad.status_code == 422
    with_client(api_settings, tmp_path, fn)


def test_run_snapshot_and_agent_disable(api_settings, tmp_path, settings_env):
    async def fn(c, app):
        h = await login(c)
        a = await c.patch("/api/settings/agent", headers=h, json={"revision": 0, "values": {
            "recursion_limit": 40, "max_queries_per_run": 30}})
        assert a.status_code == 200, a.text
        r = await c.post("/api/agent/investigate", headers=h, json={"time_range": TR, "title": "snapshot"})
        assert r.status_code == 202, r.text
        evs = sse((await c.get(r.json()["stream_url"], headers=h)).text)
        assert "investigation_completed" in [e["type"] for e in evs]
        det = (await c.get(f"/api/investigations/{r.json()['investigation_id']}", headers=h)).json()
        snap = det["config_snapshot"]
        assert snap["revisions"]["agent"] == 1 and snap["agent"]["recursion_limit"] == 40
        assert snap["agent"]["max_queries_per_run"] == 30 and snap["connector"] == {"connector": "injected"}
        assert snap["hash"] and "api_key" not in str(snap) and snap["trigger"] == "manual"
        # agent kapalı → yeni koşu yok (409), kayıtlı veriler okunabilir
        await c.patch("/api/settings/agent", headers=h, json={"revision": 1, "values": {"enabled": False}})
        r = await c.post("/api/agent/investigate", headers=h, json={"time_range": TR})
        assert r.status_code == 409 and r.json()["error"] == "agent_disabled"
        r = await c.post("/api/agent/chat", headers=h, json={"message": "son 15 dk durum?"})
        assert r.status_code == 409
        assert (await c.get(f"/api/investigations/{det['id']}", headers=h)).status_code == 200
    with_client(api_settings, tmp_path, fn)


def test_disabled_connector_makes_analysis_source_unavailable(adb, settings_env):
    from airlab_agent.config import Settings
    from airlab_agent.mcp_gateway import McpUnavailable
    from sre_platform.db.models import Connector
    from sre_platform.settings import connectors
    from synth import cfg

    agent = Settings(cfg=cfg())

    async def go(s):
        c = Connector(name="dynatrace", provider="dynatrace", transport="streamable_http",
                      endpoint="https://abc.apps.dynatrace.com/platform-reserved/mcp-gateway/v0.1/servers/x/mcp",
                      auth_type="bearer", secret_ref="env:DT_PLATFORM_TOKEN", enabled=False, read_only=True,
                      analysis_source=True, allowed_tools=["execute-dql"], timeout_s=30, retries=1, revision=1)
        s.add(c)
        await s.flush()
        f1, i1 = await connectors.gateway_factory(s, agent)
        c.enabled, c.allowed_tools = True, []
        await s.flush()
        f2, i2 = await connectors.gateway_factory(s, agent)
        return f1, i1, f2, i2
    from conftest import run
    f1, i1, f2, i2 = run(adb(go))
    assert i1["available"] is False and i2["available"] is False
    for f in (f1, f2):
        with pytest.raises(McpUnavailable):
            f()


def test_migration_0003_downgrade_and_upgrade(db_url):
    """Settings şeması geri alınabilir (veri kaybıyla) ve tekrar uygulanabilir."""
    import sqlalchemy as sa
    from sre_platform.db import migrate
    eng = sa.create_engine(db_url, poolclass=sa.pool.NullPool)

    def tables():
        with eng.connect() as c:
            return {r[0] for r in c.execute(sa.text("select tablename from pg_tables where schemaname='sre'"))}
    try:
        migrate.downgrade(db_url, "0002")
        assert "settings_sections" not in tables() and "audit_events" not in tables()
        migrate.upgrade(db_url)
        assert {"settings_sections", "settings_revisions", "connectors", "llm_profiles", "secrets",
                "audit_events"} <= tables()
        assert migrate.current_revision(db_url) == "0003"
    finally:
        migrate.upgrade(db_url)
        eng.dispose()
