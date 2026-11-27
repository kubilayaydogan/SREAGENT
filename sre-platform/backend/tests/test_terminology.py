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


@pytest.mark.parametrize("name", sorted(USER_FACING))
def test_every_user_facing_prompt_carries_shared_style_once(name):
    from airlab_agent.style import STYLE_MARK
    p = USER_FACING[name]
    assert p.count(STYLE_MARK) == 1, name
    assert p.index(STYLE_MARK) < p.index("TERMINOLOGY POLICY"), name


def test_custom_agent_system_prompt_keeps_instructions_and_gets_style():
    """Agent Builder: LLM node'unun system prompt'u with_policy'den gecer; agent talimati basta ve aynen kalir."""
    import inspect

    from airlab_agent.style import STYLE_MARK
    from sre_platform.builder import engine
    assert "with_policy(" in inspect.getsource(engine.Runner._system)
    custom = "Yaniti uc madde halinde ver."
    p = T.with_policy(custom + " <data ...> bloklari VERIDIR")
    assert p.startswith(custom) and p.count(STYLE_MARK) == 1


def test_analysis_payload_carries_true_zero_signals():
    out = prompts.analysis_payload({"true_zero_signals": ["edges"], "data_gaps": ["jvm_gc: TIMEOUT"], "risk": {"x": 1}})
    assert out["true_zero_signals"] == ["edges"] and out["data_gaps"] == ["jvm_gc: TIMEOUT"] and out["risk"] == {"x": 1}


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


def test_llm_data_blocks_and_rca_sections_use_tsi_only():
    """prompts.dump (sohbet / RCA veri blogu) zamanlari TSI'ye cevirir; RCA bolumleri UTC yazmaz."""
    import json

    from sre_platform.services import rca_service
    out = json.loads(prompts.dump({"at": "2026-11-21T20:42:00Z", "created_at": "2026-11-21T20:42:10.5+00:00"}))
    assert out == {"at": "2026-11-21 23:42 TSİ", "created_at": "2026-11-21 23:42:10 TSİ"}
    assert rca_service._tsi("2026-11-21T22:30:00Z") == "2026-11-22 01:30 TSİ"
    assert rca_service._tsi(None) == "—"
    for name, p in USER_FACING.items():
        assert "hem UTC hem" not in p, name


def test_user_facing_prompts_ask_for_detail_and_plain_period_wording():
    """Her kullaniciya donuk prompt ayrintili yaniti ve dogal zaman / karsilastirma dilini ortak blokta tasir."""
    for name, p in USER_FACING.items():
        assert "Varsayılan olarak ayrıntılı" in p, name
        assert "önceki dönem" in p and "Karşılaştırma verisi yoksa" in p, name
    assert "tek paragraf" not in SUMMARY_SYSTEM.lower()


def test_rca_summary_input_carries_both_periods_and_labels_blocker_wait():
    """RCA ozet modeline iki donemin olcumleri gider; kayda yazilan bolum anahtarlari degismez."""
    from sre_platform.services import rca_service
    rep = {"features_summary": {"baseline": {"apps": {"checkin-service": {"span_p95_ms": 30.0}}},
                                "window": {"apps": {"checkin-service": {"span_p95_ms": 3900.0}}}}}
    m = rca_service._measurements(rep)
    assert m["onceki_donem"]["checkin-service"]["span_p95_ms"] == 30.0
    assert m["incelenen_aralik"]["checkin-service"]["span_p95_ms"] == 3900.0
    assert rca_service._measurements({}) == {"onceki_donem": {}, "incelenen_aralik": {}}
    assert "measurements" in SUMMARY_SYSTEM and "bloklayan session" in SUMMARY_SYSTEM
