"""Etkili yapılandırma: model önceliği (üst bar seçimi > görev LLM profili > ortam), allowlist, seçimin kaldırılması,
manuel ve zamanlanmış koşunun gerçekten kullandığı değerlerin run kaydına yazılması.
Gerçek PostgreSQL; sahte MCP / Claude (test_api yardımcıları)."""
from __future__ import annotations

from types import SimpleNamespace as NS

import pytest

from test_api import TR, api_settings, lg_ready, login, sse, with_client  # noqa: F401

OPUS, SONNET, HAIKU = "claude-opus-5-5", "claude-sonnet-5-5", "claude-haiku-5-5"


class FakeCore:
    """Paylaşılan Llm çekirdeğinin model seçimi yüzeyi (ağ yok)."""

    def __init__(self):
        self.override, self.profiles = None, {}
        self.callbacks = []

    def set_model(self, m):
        self.override = m

    def set_profiles(self, p):
        self.profiles = dict(p or {})

    @property
    def model_name(self):
        return self.override or self.profiles.get("chat", {}).get("model") or OPUS

    @property
    def rca_model_name(self):
        return self.override or self.profiles.get("rca", {}).get("model") or OPUS


def _port():
    return NS(llm=FakeCore(), model_name=OPUS, rca_model_name=OPUS)


async def _profile(rt, name: str, model: str):
    from sre_platform.db.models import LlmProfile
    from sre_platform.db.session import transaction
    async with transaction(rt.sm) as s:
        s.add(LlmProfile(name=name, provider="anthropic", model=model, secret_ref="env:SRE_SECRET_EC_KEY",
                         enabled=True, revision=1, timeout_s=60, max_tokens=4096, retries=1))


def test_effective_models_priority_unit():
    from sre_platform.settings.runtime import effective_models
    rt = NS(llm=object(), agent=NS(claude_model="env-chat", claude_rca_model="env-rca"))
    kw = dict(has_llm=True, llm_enabled=True)
    env = effective_models(rt, profiles={}, prof_snap={}, override=None, **kw)
    assert env["chat"] == {"model": "env-chat", "source": "env"} and env["rca"]["model"] == "env-rca"
    prof = effective_models(rt, profiles={"rca": {"model": HAIKU}}, prof_snap={"rca": {"profile": "p", "model": HAIKU}},
                            override=None, **kw)
    assert prof["rca"] == {"model": HAIKU, "source": "profile", "profile": "p"} and prof["chat"]["source"] == "env"
    over = effective_models(rt, profiles={"rca": {"model": HAIKU}}, prof_snap={"rca": {"profile": "p", "model": HAIKU}},
                            override=SONNET, **kw)
    assert over["chat"]["model"] == over["rca"]["model"] == SONNET and over["rca"]["source"] == "override"
    # profil kullanılamıyorsa üst bar seçimi o görevi YENİDEN açmaz (bütçe / devre dışı kararı korunur)
    off = effective_models(rt, profiles={"rca": None}, prof_snap={"rca": {"profile": "p", "budget_exhausted": True}},
                           override=SONNET, **kw)
    assert off["rca"]["model"] is None and off["rca"]["reason"] == "profile_budget_exhausted"
    assert off["chat"]["model"] == SONNET
    dis = effective_models(rt, profiles={}, prof_snap={}, override=SONNET, has_llm=False, llm_enabled=False)
    assert dis["chat"] == {"model": None, "source": "none", "reason": "llm_disabled"}
    none = effective_models(NS(llm=None, agent=rt.agent), profiles={}, prof_snap={}, override=None, has_llm=False,
                            llm_enabled=True)
    assert none["rca"]["reason"] == "llm_not_configured"


def test_run_config_model_source_and_override_allowlist(api_settings, tmp_path, monkeypatch):
    monkeypatch.setenv("SRE_SECRET_EC_KEY", "test-only-key")

    async def fn(c, app):
        from sre_platform.settings import runtime as srt
        h = await login(c)
        rt = app.state.rt
        await _profile(rt, "rca-haiku", HAIKU)
        base = await srt.run_config(rt, trigger="manual")                              # A: ortam
        r = await c.patch("/api/settings/llm", headers=h, json={"revision": 0, "values": {"rca_profile": "rca-haiku"}})
        assert r.status_code == 200, r.text
        prof = await srt.run_config(rt, trigger="scheduled")                           # B: görev profili
        st = await c.put("/api/system/llm-model", headers=h, json={"model": SONNET})   # C: üst bar seçimi
        assert st.status_code == 200, st.text
        over_manual = await srt.run_config(rt, trigger="manual")
        over_sched = await srt.run_config(rt, trigger="scheduled")
        # allowlist daralırsa kayıtlı seçim yok sayılır (profil / ortam geçerli) ve görünür şekilde not edilir
        import dataclasses
        ps0 = rt.ps
        rt.ps = dataclasses.replace(ps0, llm_model_allowlist=(OPUS, HAIKU))
        denied = await srt.run_config(rt, trigger="manual")
        st_denied = (await c.get("/api/system/llm-model", headers=h)).json()
        put_denied = await c.put("/api/system/llm-model", headers=h, json={"model": SONNET})
        patch_denied = await c.patch("/api/settings/llm", headers=h, json={
            "revision": (await c.get("/api/settings/llm", headers=h)).json()["revision"],
            "values": {"model_override": SONNET}})
        rt.ps = ps0
        cleared = await c.put("/api/system/llm-model", headers=h, json={"model": None})  # seçimi kaldır
        after = await srt.run_config(rt, trigger="manual")
        return base, prof, st.json(), over_manual, over_sched, denied, st_denied, put_denied, patch_denied, \
            cleared.json(), after

    base, prof, st, om, os_, denied, st_denied, put_denied, patch_denied, cleared, after = \
        with_client(api_settings, tmp_path, fn, llm=_port())
    assert base.llm_effective["rca"] == {"model": OPUS, "source": "env"}
    assert prof.llm_effective["rca"] == {"model": HAIKU, "source": "profile", "profile": "rca-haiku"}
    assert prof.llm_effective["chat"]["source"] == "env"
    assert prof.snapshot["llm"]["effective"] == prof.llm_effective
    assert st["selected"] == SONNET and st["effective"]["rca"]["source"] == "override" and st["model"] == SONNET
    # manuel ve zamanlanmış koşu aynı önceliği uygular
    for rc in (om, os_):
        assert rc.model_override == SONNET
        assert rc.llm_effective["chat"]["model"] == rc.llm_effective["rca"]["model"] == SONNET
        assert rc.llm_effective["rca"]["source"] == "override"
    assert om.snapshot["hash"] == os_.snapshot["hash"]
    assert denied.model_override is None and denied.llm_effective["rca"]["model"] == HAIKU
    assert denied.llm_effective["chat"] == {"model": OPUS, "source": "env"}
    assert denied.snapshot["llm"]["model_override_ignored"] == "model_not_allowlisted"
    assert any("izinli model listesinde değil" in n for n in denied.notes)
    assert st_denied["selected"] is None and SONNET not in {o["id"] for o in st_denied["options"]}
    assert st_denied["model"] == OPUS and st_denied["rca_model"] == HAIKU
    assert put_denied.status_code == 422 and patch_denied.status_code in (400, 422)
    assert cleared["selected"] is None and cleared["effective"]["rca"] == {"model": HAIKU, "source": "profile",
                                                                          "profile": "rca-haiku"}
    assert after.model_override is None and after.llm_effective["rca"]["source"] == "profile"


def test_manual_and_scheduled_runs_record_llm_usage_and_reason(api_settings, tmp_path):
    async def fn(c, app):
        from sqlalchemy import select

        from sre_platform import worker
        from sre_platform.db.models import AgentRun, Investigation
        from sre_platform.db.session import transaction
        h = await login(c)
        rt = app.state.rt
        # manuel: çalıştırmaya özel seçim (use_llm=false)
        r = await c.post("/api/agent/investigate", headers=h, json={"time_range": TR, "title": "llm yok", "use_llm": False})
        assert r.status_code == 202, r.text
        sse((await c.get(r.json()["stream_url"], headers=h)).text)
        man = (await c.get(f"/api/investigations/{r.json()['investigation_id']}", headers=h)).json()
        # zamanlanmış: Settings › Agent › scheduled_llm kapalı
        a = (await c.get("/api/settings/agent", headers=h)).json()
        p = await c.patch("/api/settings/agent", headers=h, json={"revision": a["revision"],
                                                                   "values": {"scheduled_llm": False}})
        assert p.status_code == 200, p.text
        out = await worker.health_check(rt, {"window_minutes": 15})
        async with transaction(rt.sm) as s:
            inv = (await s.execute(select(Investigation).where(
                Investigation.id == out["investigation_id"]))).scalar_one()
            run = (await s.execute(select(AgentRun).where(AgentRun.schedule_name == "health-check"))).scalar_one()
            sched = {"snap": inv.config_snapshot, "inv_run_id": inv.run_id, "run_id": run.run_id, "models": run.models}
        tr = await c.get(f"/api/agent-traces/{run.run_id}", headers=h)
        return man, sched, out, tr.status_code

    man, sched, out, tr_code = with_client(api_settings, tmp_path, fn, llm=_port())
    run = man["config_snapshot"]["llm"]["run"]
    assert run == {"stage": "explain", "used": False, "model": None, "reason": "run_choice"}
    assert man["config_snapshot"]["llm"]["effective"]["rca"]["model"] == OPUS       # ayar değişmedi; bu koşu kullanmadı
    srun = sched["snap"]["llm"]["run"]
    assert srun["used"] is False and srun["reason"] == "scheduled_llm_off" and sched["snap"]["trigger"] == "scheduled"
    # zamanlanmış koşunun run kaydı investigation ile AYNI run_id'yi taşır → Agent Trace / MCP bağlantıları çalışır
    assert sched["run_id"] == sched["inv_run_id"] and sched["models"] == [] and tr_code == 200
    assert "run_id" not in out and "models" not in out


@pytest.mark.parametrize("mode,requested,used,expect", [
    ("manual", True, True, {"stage": "explain", "used": True, "model": SONNET, "source": "override"}),
    ("scheduled", False, False, {"stage": "explain", "used": False, "model": None, "reason": "scheduled_llm_off"}),
    ("watch-incident", False, False, {"stage": "explain", "used": False, "model": None, "reason": "incident_llm_off"}),
    ("chat", False, False, {"stage": "answer", "planned": True, "model": SONNET, "source": "override"}),
])
def test_llm_run_reason(mode, requested, used, expect):
    from sre_platform.settings.runtime import llm_run
    rc = NS(llm_effective={"rca": {"model": SONNET, "source": "override"}})
    assert llm_run(rc, mode=mode, requested=requested, used=used) == expect
