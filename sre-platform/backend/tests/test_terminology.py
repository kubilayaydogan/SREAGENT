"""Platform katmanında terminology policy: her kullanıcıya görünen prompt policy ile biter, streaming yanıtın tek giriş
noktası (ClaudePort.astream) policy'yi garanti eder, backend ve frontend metinlerinde kelime kelime çeviri yoktur.
DB gerektirmez."""
from __future__ import annotations

from pathlib import Path

import pytest

from airlab_agent import terminology as T
from sre_platform.agent import prompts
from sre_platform.agent.llm_port import ClaudePort
from sre_platform.services.rca_service import SUMMARY_SYSTEM

from conftest import run

BACKEND = Path(__file__).resolve().parents[1] / "sre_platform"
FRONTEND = Path(__file__).resolve().parents[2] / "frontend" / "src"

USER_FACING = {"analysis": prompts.ANALYSIS_SYSTEM, "history": prompts.HISTORY_SYSTEM,
               "followup": prompts.FOLLOWUP_SYSTEM, "rca_summary": SUMMARY_SYSTEM}


@pytest.mark.parametrize("name", sorted(USER_FACING))
def test_every_user_facing_prompt_ends_with_single_policy(name):
    p = USER_FACING[name]
    assert p.count("TERMINOLOGY POLICY") == 1, name
    assert p.rstrip().endswith(T.POLICY_PROMPT.strip()), name               # en sonda: çelişkide son söz policy'de
    base = p[: p.index("DİL VE TERMINOLOGY POLICY")]
    assert T.find_violations(base) == [], (name, [str(v) for v in T.find_violations(base)])


def test_deterministic_fallback_texts_follow_policy():
    assert T.find_violations(prompts.SMALLTALK) == []


class _Chunk:
    def __init__(self, text):
        self.content = text

    def __add__(self, other):
        return _Chunk(self.content + other.content)


class _FakeChat:
    def __init__(self):
        self.messages = None

    async def astream(self, messages, config=None):
        self.messages = messages
        for t in ("PostgreSQL üzerinde ", "Lock Wait oluşmuş."):
            yield _Chunk(t)


class _FakeLlm:
    def __init__(self):
        self.chat, self.callbacks, self.tracked = _FakeChat(), [], []
        self.settings = type("S", (), {"claude_model": "fake"})()

    def _track(self, msg):
        self.tracked.append(msg)


def test_stream_entry_point_enforces_policy_without_touching_output():
    llm = _FakeLlm()
    port = ClaudePort(llm)

    async def go():
        return [t async for t in port.astream("Sen bir asistansın.", "soru")]

    out = run(go)
    assert "".join(out) == "PostgreSQL üzerinde Lock Wait oluşmuş."                # çıktı değiştirilmez
    system = llm.chat.messages[0][1]
    assert system.count("TERMINOLOGY POLICY") == 1
    llm2 = _FakeLlm()

    async def go2():
        return [t async for t in ClaudePort(llm2).astream(prompts.ANALYSIS_SYSTEM, "soru")]

    run(go2)
    assert llm2.chat.messages[0][1].count("TERMINOLOGY POLICY") == 1               # çift eklenmez


def test_backend_user_facing_strings_follow_policy():
    hits = []
    for p in sorted(BACKEND.rglob("*.py")):
        if "migrations" in p.parts:                                            # şema yorumları/geçmiş revizyonlar
            continue
        hits += [f"{p.relative_to(BACKEND)}:{ln}: {v}" for ln, _, v in T.scan_python_source(p.read_text(encoding="utf-8"))]
    assert not hits, "\n".join(hits)


@pytest.mark.skipif(not FRONTEND.is_dir(), reason="frontend kaynağı bu ortamda yok (backend imajı)")
def test_frontend_texts_follow_policy():
    hits = []
    for p in sorted([*FRONTEND.rglob("*.ts"), *FRONTEND.rglob("*.tsx")]):
        hits += [f"{p.relative_to(FRONTEND)}:{ln}: {v}" for ln, _, v in T.scan_ts_source(p.read_text(encoding="utf-8"))]
    assert not hits, "\n".join(hits)
