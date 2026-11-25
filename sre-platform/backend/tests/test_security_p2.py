"""Güvenlik P2 kontrolleri.

  P2.8 Security & Governance arayüzünün API'leri: kendi yetkilerim, diğer oturumları kapatma, kullanıcı oturumlarını
  kapatma (admin), güvenlik olay özeti.
"""
from __future__ import annotations

from test_api import api_settings, lg_ready, login, mkuser, with_client  # noqa: F401


def test_my_permissions_reflect_role(api_settings, tmp_path):
    async def fn(c, app):
        h = await login(c)
        v = await mkuser(c, h, "izleyici", "viewer")
        me = (await c.get("/api/auth/permissions", headers=v)).json()
        assert me == {"role": "viewer", "permissions": ["record:read"]}
        adm = (await c.get("/api/auth/permissions", headers=h)).json()
        assert "user:manage" in adm["permissions"] and len(adm["permissions"]) == 15
        assert (await c.get("/api/auth/permissions")).status_code == 401
    with_client(api_settings, tmp_path, fn)


def test_revoke_other_sessions_keeps_current(api_settings, tmp_path):
    async def fn(c, app):
        h = await login(c)
        first = await mkuser(c, h, "sre1", "sre")             # 1. oturum
        a = await login(c, "sre1", "sre1-parola-123")
        b = await login(c, "sre1", "sre1-parola-123")
        assert len((await c.get("/api/auth/sessions", headers=a)).json()) == 3
        r = await c.post("/api/auth/sessions/revoke-others", headers=a)
        assert r.status_code == 200 and r.json() == {"revoked": 2}
        assert (await c.get("/api/auth/me", headers=a)).status_code == 200          # bu oturum açık
        assert (await c.get("/api/auth/me", headers=b)).status_code == 401          # diğerleri kapandı
        assert (await c.get("/api/auth/me", headers=first)).status_code == 401
        rows = (await c.get("/api/settings/audit", headers=h, params={"action": "auth.session.revoke_others"})).json()
        assert rows["items"][0]["actor"] == "sre1"
    with_client(api_settings, tmp_path, fn)


def test_admin_revokes_all_sessions_of_user(api_settings, tmp_path):
    async def fn(c, app):
        h = await login(c)
        s = await mkuser(c, h, "sre1", "sre")
        uid = (await c.get("/api/auth/me", headers=s)).json()["id"]
        assert (await c.post(f"/api/users/{uid}/sessions/revoke", headers=s)).status_code == 403   # sre yetkisiz
        r = await c.post(f"/api/users/{uid}/sessions/revoke", headers=h)
        assert r.status_code == 200 and r.json()["revoked"] == 1
        assert (await c.get("/api/auth/me", headers=s)).status_code == 401
        assert (await c.get(f"/api/users/{uid}/sessions", headers=h)).json() == []
        # admin kendi hesabında: bu oturum korunur
        me = (await c.get("/api/auth/me", headers=h)).json()["id"]
        await c.post(f"/api/users/{me}/sessions/revoke", headers=h)
        assert (await c.get("/api/auth/me", headers=h)).status_code == 200
    with_client(api_settings, tmp_path, fn)


def test_security_event_summary(api_settings, tmp_path):
    async def fn(c, app):
        h = await login(c)
        v = await mkuser(c, h, "izleyici", "viewer")
        for _ in range(3):
            await c.get("/api/settings/audit", headers=v)                            # authz.denied
        await c.post("/api/auth/login", json={"username": "admin", "password": "yanlis-parola"})
        assert (await c.get("/api/settings/audit/summary", headers=v)).status_code == 403
        sm = (await c.get("/api/settings/audit/summary", headers=h, params={"hours": 24})).json()
        got = {(i["action"], i["reason_code"]): i["count"] for i in sm["items"]}
        assert got[("authz.denied", "missing_audit:read")] == 4                       # 3 + özet çağrısı
        assert got[("auth.login", "bad_credentials")] == 1
        assert sm["total"] == sum(got.values()) and all(i["last_ts"] for i in sm["items"])
        sec = (await c.get("/api/settings/security", headers=h)).json()
        assert "claude-opus-5-5" in sec["llm_model_allowlist"] and sec["dlp"]["llm"]["pii"] is True
    with_client(api_settings, tmp_path, fn)


# ------------------------------------------------------------------------------------------------ P2.2 API anahtarları
def _key(token):
    return {"Authorization": "Bearer " + token}


async def _mkkey(c, h, scopes, days=30, name="ci-bot"):
    r = await c.post("/api/api-keys", headers=h, json={"name": name, "scopes": scopes, "days": days})
    assert r.status_code == 201, r.text
    return r.json()


def test_api_key_lifecycle_and_scopes(api_settings, tmp_path):
    async def fn(c, app):
        from sqlalchemy import select
        from sre_platform.db.models import ApiKey
        h = await login(c)
        k = await _mkkey(c, h, ["record:read"])
        tok = k["token"]
        assert tok.startswith("srek_" + k["prefix"] + "_") and k["status"] == "active"
        # düz anahtar listede / DB'de yok
        lst = (await c.get("/api/api-keys", headers=h)).json()
        assert "token" not in lst["items"][0] and tok not in str(lst)
        async with app.state.rt.sm() as s:
            row = (await s.execute(select(ApiKey))).scalar_one()
            assert tok.split("_", 2)[2] not in (row.key_hash, row.prefix) and len(row.key_hash) == 64
        # scope içinde: okunur
        assert (await c.get("/api/incidents", headers=_key(tok))).status_code == 200
        assert (await c.get("/api/dashboard/summary", headers=_key(tok))).status_code == 200
        # scope dışı (sahip admin olsa bile): 403
        assert (await c.get("/api/settings/audit", headers=_key(tok))).status_code == 403
        assert (await c.patch("/api/settings/agent", headers=_key(tok),
                              json={"revision": 0, "values": {"recursion_limit": 30}})).status_code == 403
        # oturum-only uçlar anahtarı reddeder
        for m, p in (("GET", "/api/auth/me"), ("GET", "/api/auth/sessions"),
                     ("POST", "/api/auth/sessions/revoke-others"), ("GET", "/api/conversations")):
            assert (await c.request(m, p, headers=_key(tok))).status_code == 403, p
        assert (await c.post("/api/auth/password", headers=_key(tok),
                             json={"current_password": "x", "new_password": "yeni" + "sifre-" + "12345"})).status_code == 403
        # anahtar yeni anahtar üretemez / kullanıcı yönetemez
        assert (await c.post("/api/api-keys", headers=_key(tok),
                             json={"name": "x-bot", "scopes": ["record:read"]})).status_code == 403
        aud = (await c.get("/api/settings/audit", headers=h, params={"action": "authz.denied"})).json()["items"]
        assert {"scope_missing_audit:read", "apikey_not_allowed"} <= {a["reason_code"] for a in aud}
        assert any(a["actor_type"] == "service" for a in aud)
        # iptal → 401
        assert (await c.delete(f"/api/api-keys/{k['id']}", headers=h)).json()["status"] == "revoked"
        assert (await c.get("/api/incidents", headers=_key(tok))).status_code == 401
        rej = (await c.get("/api/settings/audit", headers=h, params={"action": "auth.apikey.rejected"})).json()["items"]
        assert rej[0]["reason_code"] == "apikey_revoked" and rej[0]["actor"] == f"apikey:{k['prefix']}"
        assert (await c.get("/api/api-keys", headers=h)).json()["items"] == []
        assert len((await c.get("/api/api-keys", headers=h, params={"include_inactive": True})).json()["items"]) == 1
    with_client(api_settings, tmp_path, fn)


def test_api_key_rejections(api_settings, tmp_path):
    async def fn(c, app):
        import uuid
        from datetime import datetime, timedelta, timezone
        from sre_platform.db.models import ApiKey
        h = await login(c)
        sre = await mkuser(c, h, "sre1", "sre")
        bad = [{"name": "x-bot", "scopes": ["user:manage"]},           # izinli scope dışı
               {"name": "x-bot", "scopes": ["secret:manage"]},
               {"name": "x-bot", "scopes": []},
               {"name": "x-bot", "scopes": ["record:read"], "days": 0},
               {"name": "x-bot", "scopes": ["record:read"], "days": 366},
               {"name": "x-bot", "scopes": ["record:read"], "extra": 1}]
        for body in bad:
            assert (await c.post("/api/api-keys", headers=h, json=body)).status_code == 422, body
        assert (await c.post("/api/api-keys", headers=sre, json={"name": "x-bot", "scopes": ["record:read"]})
                ).status_code == 403                                     # sre anahtar üretemez
        k = await _mkkey(c, h, ["record:read", "agent:run"])
        tok = k["token"]
        secret = tok.split("_", 2)[2]
        for t in ("srek_" + k["prefix"] + "_yanlis", "srek_00000000_" + secret, "srek_kisa",
                  tok[:-1] + ("A" if tok[-1] != "A" else "B")):
            assert (await c.get("/api/incidents", headers=_key(t))).status_code == 401, t
        async with app.state.rt.sm() as s, s.begin():
            row = await s.get(ApiKey, uuid.UUID(k["id"]))
            row.expires_at = datetime.now(timezone.utc) - timedelta(minutes=1)
        assert (await c.get("/api/incidents", headers=_key(tok))).status_code == 401
        aud = (await c.get("/api/settings/audit", headers=h, params={"action": "auth.apikey.rejected"})).json()["items"]
        assert {"apikey_expired", "apikey_bad_secret", "apikey_unknown", "apikey_malformed"} <= {
            a["reason_code"] for a in aud}
    with_client(api_settings, tmp_path, fn)


def test_api_key_follows_owner(api_settings, tmp_path):
    """Etkin yetki = scope ∩ sahibin o anki rolü; sahip devre dışı → anahtarlar kalıcı iptal."""
    async def fn(c, app):
        h = await login(c)
        h2 = await mkuser(c, h, "adm2", "admin")
        uid = (await c.get("/api/auth/me", headers=h2)).json()["id"]
        k = await _mkkey(c, h2, ["record:read", "audit:read", "knowledge:manage"])
        tok = k["token"]
        assert (await c.get("/api/settings/audit", headers=_key(tok))).status_code == 200
        # sahip viewer'a düşürülür → audit:read artık yok, record:read sürer
        assert (await c.patch(f"/api/users/{uid}", headers=h, json={"role": "viewer"})).status_code == 200
        assert (await c.get("/api/settings/audit", headers=_key(tok))).status_code == 403
        assert (await c.get("/api/incidents", headers=_key(tok))).status_code == 200
        # devre dışı → iptal; yeniden etkinleştirme anahtarı canlandırmaz
        await c.patch(f"/api/users/{uid}", headers=h, json={"is_active": False})
        assert (await c.get("/api/incidents", headers=_key(tok))).status_code == 401
        await c.patch(f"/api/users/{uid}", headers=h, json={"is_active": True})
        assert (await c.get("/api/incidents", headers=_key(tok))).status_code == 401
        allk = (await c.get("/api/api-keys", headers=h, params={"include_inactive": True})).json()["items"]
        assert allk[0]["revoked_reason"] == "owner_disabled"
    with_client(api_settings, tmp_path, fn)


# ------------------------------------------------------------------------------------- P2.3 SIEM iletimi + alarmlar
SIEM_URL = "https://siem.example.com/hook"


def _siem_env(monkeypatch, resolve_to="93.184.216.34"):
    from sre_platform.settings import netguard
    monkeypatch.setenv("SRE_SECRET_SIEM_URL", SIEM_URL)
    monkeypatch.setenv("SRE_SECRET_SIEM_SIGN", "imza" + "-anahtari-" + "test")
    monkeypatch.setattr(netguard, "_resolve", lambda host, port: [resolve_to])


async def _notif(c, h, **values):
    cur = (await c.get("/api/settings/notifications", headers=h)).json()
    r = await c.patch("/api/settings/notifications", headers=h, json={"revision": cur["revision"], "values": values})
    return r


def test_security_ops_disabled_by_default(api_settings, tmp_path, monkeypatch):
    async def fn(c, app):
        from sre_platform.services import security_ops
        sent = []
        monkeypatch.setattr(security_ops, "_send", lambda *a: sent.append(a))
        h = await login(c)
        out = await security_ops.run(app.state.rt, {})
        assert out == {"siem": "disabled", "alerts": []} and sent == []
        st = (await c.get("/api/settings/security", headers=h)).json()["security_ops"]
        assert st["siem"]["enabled"] is False and st["alerts"]["enabled"] is False
        assert {r["rule"] for r in st["alerts"]["rules"]} >= {"login_failures", "authz_denied", "policy_denied"}
        # SIEM açılamaz: referans yok
        assert (await _notif(c, h, siem_enabled=True)).status_code == 422
    with_client(api_settings, tmp_path, fn)


def test_siem_forwarding_at_least_once(api_settings, tmp_path, monkeypatch):
    _siem_env(monkeypatch)

    async def fn(c, app):
        import json
        from sre_platform.services import security_ops
        sent, fail = [], {"on": False}

        async def fake_send(url, body, signing, batch_id):
            if fail["on"]:
                raise RuntimeError("HTTP 503")
            sent.append((url, json.loads(body), signing, batch_id))
        monkeypatch.setattr(security_ops, "_send", fake_send)
        h = await login(c)
        await c.get("/api/auth/me", headers=h)
        r = await _notif(c, h, siem_enabled=True, siem_url_ref="env:SRE_SECRET_SIEM_URL",
                         siem_signing_ref="env:SRE_SECRET_SIEM_SIGN", siem_batch_size=10)
        assert r.status_code == 200, r.text
        rt = app.state.rt
        assert (await security_ops.run(rt, {}))["sent"] == 0                       # ilk açılış: geçmiş gönderilmez
        v = await mkuser(c, h, "izleyici", "viewer")
        for _ in range(12):
            await c.get("/api/settings/audit", headers=v)                            # 12 authz.denied
        out = await security_ops.run(rt, {})
        assert out["siem"] == "ok" and out["sent"] >= 13 and len(sent) >= 2            # batch'lere bölündü
        events = [e for _, b, _, _ in sent for e in b["events"]]
        ids = [e["id"] for e in events]
        assert ids == sorted(ids) and len(ids) == len(set(ids))
        assert sent[0][0] == SIEM_URL and sent[0][2] and sent[0][1]["source"] == "airlab-sre-platform"
        assert {"user.create", "authz.denied"} <= {e["action"] for e in events}
        assert all("password" not in json.dumps(e).lower() or "redacted" in json.dumps(e).lower() for e in events)
        # hata: cursor ilerlemez, tek audit; düzelince kaldığı yerden
        n = len(ids)
        await c.get("/api/settings/audit", headers=v)
        fail["on"] = True
        assert (await security_ops.run(rt, {}))["siem"] == "error"
        assert (await security_ops.run(rt, {}))["siem"] == "error"
        fails = (await c.get("/api/settings/audit", headers=h, params={"action": "siem.delivery_failed"})).json()
        assert fails["total"] == 1
        st = (await c.get("/api/settings/security", headers=h)).json()["security_ops"]["siem"]
        assert st["failing"] and st["lag"] >= 1
        fail["on"] = False
        assert (await security_ops.run(rt, {}))["siem"] == "ok"
        again = [e["id"] for _, b, _, _ in sent for e in b["events"]][n:]
        assert again and min(again) > max(ids)                                      # kayıp yok, tekrar yok
        rec = (await c.get("/api/settings/audit", headers=h, params={"action": "siem.delivery_recovered"})).json()
        assert rec["total"] == 1
    with_client(api_settings, tmp_path, fn)


def test_siem_signature_and_ssrf(api_settings, tmp_path, monkeypatch):
    from sre_platform.services import security_ops
    sig = security_ops.sign("k", b'{"a":1}')
    import hashlib
    import hmac
    assert sig == "sha256=" + hmac.new(b"k", b'{"a":1}', hashlib.sha256).hexdigest()
    _siem_env(monkeypatch, resolve_to="10.0.0.5")                                    # iç ağ adresi

    async def fn(c, app):
        h = await login(c)
        r = await _notif(c, h, slack_enabled=False, siem_enabled=True, siem_url_ref="env:SRE_SECRET_SIEM_URL")
        assert r.status_code == 422 and "SIEM adresi reddedildi" in r.text          # kayıtta engel
    with_client(api_settings, tmp_path, fn)


def test_security_alert_rules_and_cooldown(api_settings, tmp_path, monkeypatch):
    _siem_env(monkeypatch)

    async def fn(c, app):
        from sre_platform.services import security_ops
        slack = []

        async def fake_post(url, payload, attempts=3):
            slack.append(payload["text"])
        monkeypatch.setattr("sre_platform.services.slack_notify._post", fake_post)
        h = await login(c)
        r = await _notif(c, h, security_alerts_enabled=True, alert_authz_denied=3, alert_window_minutes=15)
        assert r.status_code == 200, r.text
        v = await mkuser(c, h, "izleyici", "viewer")
        rt = app.state.rt
        await c.get("/api/settings/audit", headers=v)
        assert (await security_ops.run(rt, {}))["alerts"] == []                      # eşik altında
        for _ in range(3):
            await c.get("/api/settings/audit", headers=v)
        fired = (await security_ops.run(rt, {}))["alerts"]
        assert [f["rule"] for f in fired] == ["authz_denied"] and fired[0]["count"] >= 3
        assert (await security_ops.run(rt, {}))["alerts"] == []                      # cooldown
        if (await c.get("/api/settings/notifications", headers=h)).json()["values"]["slack_webhook_ref"]:
            assert len(slack) == 1 and "güvenlik alarmı" in slack[0]                 # Slack tanımlıysa bir kez
        al = (await c.get("/api/settings/audit", headers=h, params={"action": "security.alert"})).json()["items"]
        assert len(al) == 1 and al[0]["result"] == "alert" and al[0]["reason_code"] == "authz_denied"
        sm = (await c.get("/api/settings/audit/summary", headers=h)).json()
        assert any(i["action"] == "security.alert" for i in sm["items"])
        st = (await c.get("/api/settings/security", headers=h)).json()["security_ops"]["alerts"]
        assert next(r for r in st["rules"] if r["rule"] == "authz_denied")["last_fired"]
    with_client(api_settings, tmp_path, fn)
