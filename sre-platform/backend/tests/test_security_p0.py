"""Güvenlik P0 kontrolleri — gerçek PostgreSQL, gerçek FastAPI uygulaması, sahte MCP/LLM.

Kapsam (docs/security/SECURITY_IMPLEMENTATION_PLAN.md):
  P0.2 sunucu tarafı oturumlar (logout, iptal, idle, parola/rol değişikliği)   → T04
  P0.3 resume yolunda policy kontrolü                                          → T12
  P0.4 güvenlik audit'i + append-only                                          → T02, T14
  P0.5 çalışma anında SSRF kontrolü (MCP, LLM base_url, Slack)                 → T09
  P0.6 kalıcı login throttle, çalıştırma hız sınırı, güvenilir proxy IP'si
  P0.7 /api/docs varsayılan kapalı
"""
from __future__ import annotations

import dataclasses
import json
import uuid
from datetime import datetime, timedelta, timezone

import jwt
import pytest
import sqlalchemy as sa
from test_api import ADMIN_PW, api_settings, lg_ready, login, mkuser, with_client  # noqa: F401

from conftest import run


def _bearer(h: dict) -> str:
    return h["Authorization"].split(" ", 1)[1]


async def _audit(c, h, **params) -> list[dict]:
    r = await c.get("/api/settings/audit", headers=h, params={"size": 200, **params})
    assert r.status_code == 200, r.text
    return r.json()["items"]


def _sql(db_url: str, stmt: str, **kw):
    eng = sa.create_engine(db_url, poolclass=sa.pool.NullPool)
    try:
        with eng.begin() as c:
            return c.execute(sa.text(stmt), kw)
    finally:
        eng.dispose()


# ------------------------------------------------------------------------------------------------ P0.2 oturumlar
def test_logout_revokes_token_and_forged_tokens_are_rejected(api_settings, tmp_path):
    async def fn(c, app):
        h = await login(c)
        assert (await c.get("/api/auth/me", headers=h)).status_code == 200
        sess = (await c.get("/api/auth/sessions", headers=h)).json()
        assert len(sess) == 1 and sess[0]["current"] is True
        assert (await c.post("/api/auth/logout", headers=h)).status_code == 204
        r = await c.get("/api/auth/me", headers=h)                      # aynı token artık geçersiz
        assert r.status_code == 401 and r.json()["error"] == "unauthorized"

        # Doğru imzalı ama sunucuda oturumu olmayan token (ör. sızdırılmış secret ile üretilmiş) → reddedilir
        me_id = jwt.decode(_bearer(h), options={"verify_signature": False})["sub"]
        now = datetime.now(timezone.utc)
        forged = jwt.encode({"sub": me_id, "role": "admin", "exp": now + timedelta(hours=1), "iat": now,
                             "jti": "yok-boyle-oturum"}, api_settings.jwt_secret, algorithm="HS256")
        assert (await c.get("/api/auth/me", headers={"Authorization": f"Bearer {forged}"})).status_code == 401
        # jti'siz token (eski biçim) → reddedilir
        old = jwt.encode({"sub": me_id, "role": "admin", "exp": now + timedelta(hours=1)}, api_settings.jwt_secret,
                         algorithm="HS256")
        assert (await c.get("/api/auth/me", headers={"Authorization": f"Bearer {old}"})).status_code == 401
        # alg=none → reddedilir
        none = jwt.encode({"sub": me_id, "role": "admin", "exp": now + timedelta(hours=1), "jti": "x"}, "",
                          algorithm="none")
        assert (await c.get("/api/auth/me", headers={"Authorization": f"Bearer {none}"})).status_code == 401

        h2 = await login(c)
        rows = await _audit(c, h2, section="auth")
        actions = [(r["action"], r["result"], r["reason_code"]) for r in rows]
        assert ("auth.logout", "ok", None) in actions
        assert ("auth.session.rejected", "denied", "revoked:logout") in actions
        assert ("auth.session.rejected", "denied", "unknown_session") in actions
        assert all(r["correlation_id"] for r in rows)
    with_client(api_settings, tmp_path, fn)


def test_idle_timeout_and_expiry(api_settings, db_url, tmp_path):
    async def fn(c, app):
        h = await login(c)
        sid = jwt.decode(_bearer(h), options={"verify_signature": False})["jti"]
        _sql(db_url, "update sre.auth_sessions set last_seen_at = now() - interval '31 minutes' where id = :i", i=sid)
        assert (await c.get("/api/auth/me", headers=h)).status_code == 401           # idle 30 dk
        h2 = await login(c)
        sid2 = jwt.decode(_bearer(h2), options={"verify_signature": False})["jti"]
        _sql(db_url, "update sre.auth_sessions set expires_at = now() - interval '1 second' where id = :i", i=sid2)
        assert (await c.get("/api/auth/me", headers=h2)).status_code == 401          # mutlak süre
        h3 = await login(c)
        reasons = {r["reason_code"] for r in await _audit(c, h3, action="auth.session.rejected")}
        assert {"idle_timeout", "expired"} <= reasons
    with_client(api_settings, tmp_path, fn)


def test_password_change_and_admin_changes_revoke_sessions(api_settings, tmp_path):
    async def fn(c, app):
        h = await login(c)
        a1 = await mkuser(c, h, "ayse", "sre")
        a2 = await login(c, "ayse", "ayse-parola-123")
        # yanlış mevcut parola → 403 + audit
        r = await c.post("/api/auth/password", headers=a1, json={"current_password": "yanlis-parola-x",
                                                                 "new_password": "yeni-parola-12345"})
        assert r.status_code == 403
        r = await c.post("/api/auth/password", headers=a1, json={"current_password": "ayse-parola-123",
                                                                 "new_password": "yeni-parola-12345"})
        assert r.status_code == 204
        assert (await c.get("/api/auth/me", headers=a1)).status_code == 200          # bu oturum kalır
        assert (await c.get("/api/auth/me", headers=a2)).status_code == 401          # diğer oturum iptal
        assert (await c.post("/api/auth/login", json={"username": "ayse", "password": "ayse-parola-123"})
                ).status_code == 401                                                  # eski parola geçmez

        # Başkasının oturumunu iptal edemez (varlık da sızdırılmaz → 404)
        admin_sid = jwt.decode(_bearer(h), options={"verify_signature": False})["jti"]
        assert (await c.delete(f"/api/auth/sessions/{admin_sid}", headers=a1)).status_code == 404

        users = (await c.get("/api/users", headers=h)).json()["items"]
        uid = next(u["id"] for u in users if u["username"] == "ayse")
        # rol değişikliği → tüm oturumlar iptal
        assert (await c.patch(f"/api/users/{uid}", headers=h, json={"role": "viewer"})).status_code == 200
        assert (await c.get("/api/auth/me", headers=a1)).status_code == 401
        # admin parola sıfırlama → yeni oturum da iptal
        a3 = await login(c, "ayse", "yeni-parola-12345")
        assert (await c.patch(f"/api/users/{uid}", headers=h, json={"password": "sifirlanan-parola-1"})
                ).status_code == 200
        assert (await c.get("/api/auth/me", headers=a3)).status_code == 401
        # devre dışı
        a4 = await login(c, "ayse", "sifirlanan-parola-1")
        assert (await c.patch(f"/api/users/{uid}", headers=h, json={"is_active": False})).status_code == 200
        assert (await c.get("/api/auth/me", headers=a4)).status_code == 401
        assert (await c.get(f"/api/users/{uid}/sessions", headers=h)).json() == []

        rows = await _audit(c, h, section="users")
        acts = [r["action"] for r in rows]
        assert {"user.create", "user.role_change", "user.update", "user.disable"} <= set(acts)
        role = next(r for r in rows if r["action"] == "user.role_change")
        assert role["before"] == {"role": "sre"} and role["after"] == {"role": "viewer"}
        dump = json.dumps(await _audit(c, h))
        for secret in ("ayse-parola-123", "yeni-parola-12345", "sifirlanan-parola-1", ADMIN_PW):
            assert secret not in dump                                                 # parolalar audit'e girmez
        pw = await _audit(c, h, action="auth.password.change")
        assert {(r["result"], r["reason_code"]) for r in pw} == {("ok", None), ("denied", "bad_current_password")}
    with_client(api_settings, tmp_path, fn)


# ------------------------------------------------------------------------------------------------ P0.6 throttle
def test_login_throttle_survives_restart(api_settings, tmp_path):
    async def first(c, app):
        h = await login(c)
        await mkuser(c, h, "izleyici", "viewer")
        for _ in range(5):
            r = await c.post("/api/auth/login", json={"username": "izleyici", "password": "yanlis-parola"})
            assert r.status_code == 401
        r = await c.post("/api/auth/login", json={"username": "izleyici", "password": "izleyici-parola-123"})
        assert r.status_code == 429 and r.headers.get("Retry-After")
    with_client(api_settings, tmp_path, first)

    async def after_restart(c, app):                                               # yeni uygulama örneği
        r = await c.post("/api/auth/login", json={"username": "izleyici", "password": "izleyici-parola-123"})
        assert r.status_code == 429
        h = await login(c)                                                          # başka kullanıcı etkilenmez
        rows = await _audit(c, h, action="auth.login")
        reasons = [r["reason_code"] for r in rows if r["result"] == "denied"]
        assert reasons.count("bad_credentials") == 5 and reasons.count("throttled") == 2
        assert all(r["actor"].startswith("anonymous:") for r in rows if r["result"] == "denied")
    with_client(api_settings, tmp_path, after_restart)


def test_client_ip_trusts_only_configured_proxies():
    from sre_platform.security.context import parse_networks, resolve_client_ip
    trusted = parse_networks(["172.16.0.0/12"])
    # güvenilmeyen eş: istemcinin X-Forwarded-For'u yok sayılır
    assert resolve_client_ip("203.0.113.9", "1.2.3.4", trusted) == "203.0.113.9"
    # güvenilen proxy: en sağdaki (proxy'nin eklediği) adres; istemcinin soldaki sahte değeri değil
    assert resolve_client_ip("172.20.0.5", "1.2.3.4, 198.51.100.7", trusted) == "198.51.100.7"
    assert resolve_client_ip("172.20.0.5", "çöp", trusted) == "172.20.0.5"
    assert resolve_client_ip("172.20.0.5", None, ()) == "172.20.0.5"


def test_run_rate_limit_is_enforced_and_audited(api_settings, tmp_path):
    ps = dataclasses.replace(api_settings, run_rate_per_minute=1)

    async def fn(c, app):
        h = await login(c)
        r1 = await c.post("/api/agent/chat", headers=h, json={"message": "checkin durumu"})
        assert r1.status_code == 202, r1.text
        r2 = await c.post("/api/agent/chat", headers=h, json={"message": "reservation durumu"})
        assert r2.status_code == 429 and r2.json()["error"] == "rate_limited"
        await c.get(f"/api/agent/stream/{r1.json()['run_id']}", headers=h)        # ilk koşunun bitmesini bekle
        rows = await _audit(c, h, section="agent")
        assert [r["action"] for r in rows if r["result"] == "ok"] == ["run.start"]
        assert any(r["run_id"] == r1.json()["run_id"] for r in rows)
        assert any(r["action"] == "run.denied" and r["reason_code"] == "rate_limited" for r in rows)
    with_client(ps, tmp_path, fn)


# ------------------------------------------------------------------------------------------------ P0.3 resume
def test_resume_respects_agent_kill_switch(api_settings, tmp_path):
    async def fn(c, app):
        h = await login(c)
        conv = (await c.post("/api/conversations", headers=h, json={"title": "deneme"})).json()
        rev = (await c.get("/api/settings/agent", headers=h)).json()["revision"]
        r = await c.patch("/api/settings/agent", headers=h, json={"revision": rev, "values": {"enabled": False}})
        assert r.status_code == 200, r.text
        for path, body in ((f"/api/conversations/{conv['id']}/resume", None),
                           ("/api/agent/chat", {"message": "x", "conversation_id": conv["id"]})):
            r = await c.post(path, headers=h, json=body)
            assert r.status_code == 409 and r.json()["error"] == "agent_disabled", (path, r.text)
        rows = await _audit(c, h, action="run.denied")
        assert {r["target"] for r in rows} >= {f"POST /api/conversations/{conv['id']}/resume", "POST /api/agent/chat"}
        assert all(r["reason_code"] == "policy_agent_settings" and r["actor"] == "admin" for r in rows)
    with_client(api_settings, tmp_path, fn)


# ------------------------------------------------------------------------------------------------ P0.4 audit
def test_forbidden_is_audited_and_audit_is_append_only(api_settings, db_url, tmp_path):
    async def fn(c, app):
        h = await login(c)
        v = await mkuser(c, h, "izleyici", "viewer")
        assert (await c.get("/api/users", headers=v)).status_code == 403
        rows = await _audit(c, h, action="authz.denied")
        assert rows and rows[0]["actor"] == "izleyici" and rows[0]["reason_code"] == "requires_admin"
        assert rows[0]["target"] == "GET /api/users" and rows[0]["source_ip"]
    with_client(api_settings, tmp_path, fn)
    for stmt in ("update sre.audit_events set actor = 'x'", "delete from sre.audit_events",
                 "truncate sre.audit_events"):
        with pytest.raises(sa.exc.DBAPIError, match="append-only"):
            _sql(db_url, stmt)
    assert _sql(db_url, "select count(*) from sre.audit_events").scalar() > 0


def test_approval_and_export_events_are_audited(api_settings, tmp_path):
    async def fn(c, app):
        h = await login(c)
        s1 = await mkuser(c, h, "sre1", "sre")
        a = (await c.post("/api/approvals", headers=h, json={"action_type": "RESTART", "target": "checkin-service",
                                                             "justification": "pool tükendi, yeniden başlat"})).json()
        r = await c.post(f"/api/approvals/{a['id']}/decision", headers=h, json={"approve": True})
        assert r.status_code == 409                                                   # dört göz
        b = (await c.post("/api/approvals", headers=s1, json={"action_type": "RESTART", "target": "checkin-service",
                                                              "justification": "ikinci istek gerekçesi"})).json()
        assert (await c.post(f"/api/approvals/{b['id']}/decision", headers=h, json={"approve": False})
                ).status_code == 200
        rows = await _audit(c, h, section="approvals")
        got = {(r["action"], r["result"], r["reason_code"]) for r in rows}
        assert {("approval.request", "ok", None), ("approval.decide", "denied", "four_eyes"),
                ("approval.decide", "ok", None)} <= got
    with_client(api_settings, tmp_path, fn)


# ------------------------------------------------------------------------------------------------ P0.5 SSRF
def test_runtime_egress_guard_blocks_private_targets(adb, monkeypatch, tmp_path):
    from airlab_agent.config import Settings
    from airlab_agent.mcp_gateway import McpUnavailable
    from synth import cfg

    from sre_platform.db.models import AuditEvent, Connector, LlmProfile
    from sre_platform.settings import connectors, llm_profiles, netguard

    # Kayıtta public görünen host, çalışma anında iç adrese çözülüyor (DNS rebinding senaryosu)
    monkeypatch.setattr(netguard, "_resolve", lambda host, port: ["10.0.0.5"])
    monkeypatch.setenv("DT_PLATFORM_TOKEN", "dt-test-token-value")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "anthropic-test-value")

    async def go(s):
        s.add(Connector(name="dynatrace", provider="dynatrace", transport="streamable_http",
                        endpoint="https://abc.apps.dynatrace.com/mcp", auth_type="bearer",
                        secret_ref="env:DT_PLATFORM_TOKEN", enabled=True, read_only=True, analysis_source=True,
                        allowed_tools=["execute-dql"], timeout_s=30, retries=1, revision=1))
        s.add(LlmProfile(name="ozel", provider="anthropic", model="claude-opus-5-5",
                         base_url="https://llm.example.com", secret_ref="env:ANTHROPIC_API_KEY", enabled=True,
                         revision=1))
        await s.flush()
        f, info = await connectors.gateway_factory(s, Settings(cfg=cfg()))
        profiles, snap, notes = await llm_profiles.task_profiles(s, {"chat_profile": "ozel", "rca_profile": None})
        ev = (await s.execute(sa.select(AuditEvent).where(AuditEvent.action == "egress.denied"))).scalars().all()
        return f, info, profiles, snap, notes, [(e.section, e.target, e.reason_code) for e in ev]
    f, info, profiles, snap, notes, ev = run(adb(go))
    assert info["available"] is False and info["egress_denied"] is True
    with pytest.raises(McpUnavailable, match="engellendi"):
        f()
    assert profiles["chat"] is None and snap["chat"]["egress_denied"] and any("engellendi" in n for n in notes)
    assert ("connectors", "dynatrace", "ssrf_guard") in ev and ("llm", "ozel", "ssrf_guard") in ev


def test_slack_post_rechecks_destination(monkeypatch):
    from sre_platform.services import slack_notify
    from sre_platform.settings import netguard
    monkeypatch.setattr(netguard, "_resolve", lambda host, port: ["169.254.169.254"])     # metadata servisi

    async def go():
        await slack_notify._post("https://hooks.example.com/services/x", {"text": "t"})
    with pytest.raises(netguard.UnsafeUrl):
        run(go)


# ------------------------------------------------------------------------------------------------ P0.7 API yüzeyi
def test_api_docs_disabled_by_default(api_settings, tmp_path):
    async def fn(c, app):
        assert (await c.get("/api/openapi.json")).status_code == 404
        assert (await c.get("/api/docs")).status_code == 404
    with_client(api_settings, tmp_path, fn)

    async def on(c, app):
        assert (await c.get("/api/openapi.json")).status_code == 200
    with_client(dataclasses.replace(api_settings, api_docs=True), tmp_path, on)


def test_session_helper_reports_revocation(api_settings, tmp_path):
    from sre_platform.security import sessions

    async def fn(c, app):
        h = await login(c)
        sid = jwt.decode(_bearer(h), options={"verify_signature": False})["jti"]
        sm = app.state.rt.sm
        assert await sessions.is_active(sm, sid) is True                    # SSE keepalive yeniden doğrulaması
        await c.post("/api/auth/logout", headers=h)
        assert await sessions.is_active(sm, sid) is False
        assert await sessions.is_active(sm, None) is False
        assert await sessions.is_active(sm, str(uuid.uuid4())) is False
    with_client(api_settings, tmp_path, fn)
