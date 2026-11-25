"""Arayüzden model seçimi: yalnız yönetici değiştirir, seçim Settings'te (llm.model_override) kalıcıdır ve başka süreçte (worker) de uygulanır."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from conftest import run
from sre_platform.services import model_switch


class FakeCore:
    def __init__(self):
        self.override = None
        self.settings = SimpleNamespace(claude_model="claude-opus-5-5", claude_rca_model="claude-opus-5-5")

    def set_model(self, m):
        self.override = m

    @property
    def model_name(self):
        return self.override or self.settings.claude_model

    @property
    def rca_model_name(self):
        return self.override or self.settings.claude_rca_model


def test_select_persists_and_other_process_picks_it_up(settings):
    from sre_platform.db.session import make_engine, make_sessionmaker

    async def go():
        eng = make_engine(settings, "pytest")
        sm = make_sessionmaker(eng)
        from airlab_agent.config import load_settings
        agent = load_settings()
        backend = SimpleNamespace(sm=sm, ps=settings, agent=agent, llm=SimpleNamespace(llm=FakeCore()))
        worker = SimpleNamespace(sm=sm, ps=settings, agent=agent, llm=SimpleNamespace(llm=FakeCore()))
        try:
            before = await model_switch.refresh(worker)                       # kayıt yok → env varsayılanı
            st = await model_switch.select(backend, "claude-sonnet-5-5", username="admin")
            after = await model_switch.refresh(worker)
            with pytest.raises(ValueError):
                await model_switch.select(backend, "gpt-5", username="admin")
            back = await model_switch.select(backend, "claude-opus-5-5", username="admin")
            await model_switch.refresh(worker)
            return before, st, after, worker.llm.llm.model_name, back
        finally:
            await eng.dispose()

    before, st, after, worker_final, back = run(go)
    assert before is None
    assert st["model"] == st["rca_model"] == "claude-sonnet-5-5" and st["changed_by"] == "admin"
    assert {o["id"] for o in st["options"]} == {"claude-opus-5-5", "claude-sonnet-5-5"}
    assert after == "claude-sonnet-5-5"
    assert back["model"] == "claude-opus-5-5" and worker_final == "claude-opus-5-5"


def test_refresh_without_llm_is_noop():
    assert run(model_switch.refresh, SimpleNamespace(sm=None, llm=None)) is None
